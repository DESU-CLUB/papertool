from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urlparse
from urllib.request import Request, urlopen

from papertool.db import PaperDB
from papertool.ingest import IngestStats, ingest_file

USER_AGENT = "PaperTool/0.1 (+https://localhost)"


@dataclass(slots=True)
class ImportedResource:
    url: str
    resource_kind: str
    saved_path: str
    title: str
    paper_id: str | None


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._ignore_stack: list[str] = []
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._ignore_stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if self._ignore_stack and self._ignore_stack[-1] == tag:
            self._ignore_stack.pop()

    def handle_data(self, data: str) -> None:
        if not self._ignore_stack:
            text = data.strip()
            if text:
                self._parts.append(text)

    def text(self) -> str:
        return " ".join(self._parts)


def normalize_input_url(url: str) -> str:
    value = url.strip()
    if not value:
        raise ValueError("url is required")
    parsed = urlparse(value)
    if not parsed.scheme:
        value = f"https://{value}"
    return value


def detect_resource_kind(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()

    if host in {"arxiv.org", "www.arxiv.org"} and (path.startswith("/abs/") or path.startswith("/pdf/")):
        return "arxiv"
    if path.endswith(".pdf"):
        return "pdf"
    if host in {"github.com", "www.github.com"} and _github_repo_ref(url) is not None:
        return "github"
    if host in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"} and "/status/" in path:
        return "x_post"
    return "webpage"


def arxiv_abs_to_pdf(url: str) -> str:
    arxiv_id = extract_arxiv_id_from_url(url)
    if not arxiv_id:
        return url
    return f"https://arxiv.org/pdf/{arxiv_id}.pdf"


def canonicalize_arxiv_id(arxiv_id: str) -> str:
    normalized = arxiv_id.strip().lower().replace("arxiv:", "")
    return re.sub(r"v\d+$", "", normalized)


def extract_arxiv_id_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host not in {"arxiv.org", "www.arxiv.org"}:
        return None

    path = parsed.path.strip()
    if path.startswith("/abs/"):
        raw = path.removeprefix("/abs/")
    elif path.startswith("/pdf/"):
        raw = path.removeprefix("/pdf/")
    else:
        return None

    raw = raw.strip("/")
    if raw.endswith(".pdf"):
        raw = raw[:-4]
    if not raw:
        return None
    return raw


def _sanitize_filename(name: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._ -]+", "", name).strip()
    value = re.sub(r"\s+", "_", value)
    return value[:120] or "resource"


def _clean_title(title: str) -> str:
    compact = re.sub(r"\s+", " ", (title or "")).strip()
    compact = re.sub(r"^(title|abstract)\s*:\s*", "", compact, flags=re.IGNORECASE).strip()
    return compact


def _write_unique_bytes(root: Path, stem: str, suffix: str, content: bytes) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    safe_stem = _sanitize_filename(stem)
    candidate = root / f"{safe_stem}{suffix}"
    idx = 2
    while candidate.exists():
        candidate = root / f"{safe_stem}-{idx}{suffix}"
        idx += 1
    candidate.write_bytes(content)
    return candidate


def _write_unique_text(root: Path, stem: str, suffix: str, content: str) -> Path:
    return _write_unique_bytes(root, stem, suffix, content.encode("utf-8"))


def _http_get(url: str, *, accept: str | None = None, timeout: int = 30) -> tuple[bytes, str]:
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    req = Request(url, headers=headers)
    with urlopen(req, timeout=timeout) as response:
        data = response.read()
        content_type = response.headers.get("Content-Type", "")
    return data, content_type


def _decode_bytes(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1", errors="ignore")


def _extract_html_title(html: str) -> str | None:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    title = re.sub(r"\s+", " ", match.group(1)).strip()
    return title or None


def _extract_meta_description(html: str) -> str | None:
    patterns = [
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
        r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)
        if match:
            text = re.sub(r"\s+", " ", match.group(1)).strip()
            if text:
                return text
    return None


def _html_to_text(html: str, max_chars: int = 80_000) -> str:
    parser = _VisibleTextParser()
    parser.feed(html)
    text = re.sub(r"\s+", " ", parser.text()).strip()
    return text[:max_chars]


def _github_repo_ref(url: str) -> tuple[str, str] | None:
    parsed = urlparse(url)
    if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    return owner, repo


def _fetch_github_repo_markdown(url: str) -> tuple[str, str]:
    ref = _github_repo_ref(url)
    if not ref:
        raise ValueError("Invalid GitHub repository URL")
    owner, repo = ref
    api_base = f"https://api.github.com/repos/{owner}/{repo}"

    meta_bytes, _ = _http_get(api_base, accept="application/vnd.github+json")
    metadata = json.loads(_decode_bytes(meta_bytes))

    description = metadata.get("description") or ""
    stars = metadata.get("stargazers_count")
    default_branch = metadata.get("default_branch") or "main"

    readme = ""
    try:
        readme_bytes, _ = _http_get(f"{api_base}/readme", accept="application/vnd.github.raw")
        readme = _decode_bytes(readme_bytes)
    except (HTTPError, URLError):
        try:
            raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{default_branch}/README.md"
            readme_bytes, _ = _http_get(raw_url)
            readme = _decode_bytes(readme_bytes)
        except (HTTPError, URLError):
            readme = "README could not be fetched."

    title = f"{owner}/{repo}"
    lines = [
        f"# GitHub Repo: {title}",
        "",
        f"- Source URL: {url}",
        f"- Description: {description}",
        f"- Stars: {stars}",
        f"- Default branch: {default_branch}",
        "",
        "## README",
        "",
        readme[:200_000],
        "",
    ]
    return title, "\n".join(lines)


def _fetch_x_post_markdown(url: str) -> tuple[str, str]:
    oembed = f"https://publish.twitter.com/oembed?omit_script=true&dnt=true&url={quote_plus(url)}"
    payload_bytes, _ = _http_get(oembed, accept="application/json")
    payload = json.loads(_decode_bytes(payload_bytes))
    author = payload.get("author_name") or "Unknown"
    html_snippet = payload.get("html") or ""
    text = _html_to_text(html_snippet, max_chars=10_000)

    title = f"X Post by {author}"
    lines = [
        f"# X Post: {author}",
        "",
        f"- Source URL: {url}",
        "",
        "## Extracted Content",
        "",
        text or "No content extracted.",
        "",
    ]
    return title, "\n".join(lines)


def _render_generic_web_markdown(url: str, payload_bytes: bytes) -> tuple[str, str]:
    text = _decode_bytes(payload_bytes)
    title = _extract_html_title(text) or url
    meta = _extract_meta_description(text)
    extracted = _html_to_text(text)

    lines = [
        f"# Web Resource: {title}",
        "",
        f"- Source URL: {url}",
    ]
    if meta:
        lines.append(f"- Meta description: {meta}")
    lines.extend(
        [
            "",
            "## Extracted Content",
            "",
            extracted or "No content extracted.",
            "",
        ]
    )
    return title, "\n".join(lines)


def _ingest_saved_file(db: PaperDB, saved_path: Path) -> str | None:
    ingest_file(db, saved_path, IngestStats(scanned=1))
    row = db.get_paper_by_path(str(saved_path.resolve()))
    if not row:
        return None
    return str(row["id"])


def import_url_to_library(
    db: PaperDB,
    library_dir: Path,
    url: str,
    *,
    page_title: str | None = None,
    context_text: str | None = None,
) -> ImportedResource:
    normalized = normalize_input_url(url)
    kind = detect_resource_kind(normalized)
    captures_root = library_dir / "captures"
    clean_page_title = _clean_title(page_title or "") if page_title else None

    if kind in {"pdf", "arxiv"}:
        pdf_url = arxiv_abs_to_pdf(normalized) if kind == "arxiv" else normalized
        if kind == "arxiv":
            arxiv_id = extract_arxiv_id_from_url(normalized)
            if arxiv_id:
                canonical = canonicalize_arxiv_id(arxiv_id)
                existing = db.get_paper_by_arxiv_id(canonical)
                if existing:
                    return ImportedResource(
                        url=normalized,
                        resource_kind=kind,
                        saved_path=str(existing["path"]),
                        title=canonical,
                        paper_id=str(existing["id"]),
                    )
        pdf_bytes, _ = _http_get(pdf_url, accept="application/pdf,*/*")
        if kind == "arxiv":
            arxiv_id = extract_arxiv_id_from_url(normalized) or Path(urlparse(pdf_url).path).stem
            arxiv_id = canonicalize_arxiv_id(arxiv_id)
            title_hint = arxiv_id or "paper"
        else:
            title_hint = clean_page_title or Path(urlparse(pdf_url).path).stem or "paper"
        saved = _write_unique_bytes(captures_root / "papers", title_hint, ".pdf", pdf_bytes)
        paper_id = _ingest_saved_file(db, saved)
        if paper_id and kind == "arxiv":
            arxiv_id = extract_arxiv_id_from_url(normalized)
            if arxiv_id:
                db.set_paper_arxiv_id(paper_id, canonicalize_arxiv_id(arxiv_id))
        return ImportedResource(
            url=normalized,
            resource_kind=kind,
            saved_path=str(saved.resolve()),
            title=title_hint,
            paper_id=paper_id,
        )

    if kind == "github":
        title, markdown = _fetch_github_repo_markdown(normalized)
    elif kind == "x_post":
        title, markdown = _fetch_x_post_markdown(normalized)
    else:
        payload_bytes, content_type = _http_get(
            normalized,
            accept="text/html,application/xhtml+xml,text/plain,application/pdf,*/*",
        )
        if "pdf" in content_type.lower():
            title_hint = page_title or Path(urlparse(normalized).path).stem or "paper"
            saved = _write_unique_bytes(captures_root / "papers", title_hint, ".pdf", payload_bytes)
            paper_id = _ingest_saved_file(db, saved)
            return ImportedResource(
                url=normalized,
                resource_kind="pdf",
                saved_path=str(saved.resolve()),
                title=title_hint,
                paper_id=paper_id,
            )
        title, markdown = _render_generic_web_markdown(normalized, payload_bytes)

    if context_text:
        markdown += "\n## Capture Context\n\n" + context_text.strip() + "\n"

    file_title = clean_page_title or title
    saved = _write_unique_text(captures_root / "web", file_title, ".md", markdown)
    paper_id = _ingest_saved_file(db, saved)
    return ImportedResource(
        url=normalized,
        resource_kind=kind,
        saved_path=str(saved.resolve()),
        title=file_title,
        paper_id=paper_id,
    )


def import_result_to_dict(result: ImportedResource) -> dict[str, Any]:
    return asdict(result)
