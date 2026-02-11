from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re

from papertool.config import PaperToolConfig


INVALID_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._ -]+")


def _slugify(name: str) -> str:
    cleaned = INVALID_FILENAME_CHARS.sub("", name).strip()
    return cleaned[:120] or "untitled"


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _append_markdown(path: Path, content: str) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    path.write_text(existing + content, encoding="utf-8")


def paper_note_path(config: PaperToolConfig, title: str) -> Path:
    if not config.obsidian_vault:
        raise ValueError("obsidian_vault not configured")
    papers_dir = config.obsidian_vault / config.obsidian_papers_dir
    _ensure_dir(papers_dir)
    return papers_dir / f"{_slugify(title)}.md"


def upsert_paper_note(
    config: PaperToolConfig,
    *,
    title: str,
    source_path: str,
    summary: str,
    doi: str | None,
    arxiv_id: str | None,
) -> Path:
    note_path = paper_note_path(config, title)
    if note_path.exists():
        return note_path
    now = datetime.now().isoformat(timespec="seconds")
    safe_title = title.replace('"', "")
    safe_source = source_path.replace('"', "")
    safe_doi = (doi or "").replace('"', "")
    safe_arxiv = (arxiv_id or "").replace('"', "")
    frontmatter = [
        "---",
        f'title: "{safe_title}"',
        f'source_path: "{safe_source}"',
        f'doi: "{safe_doi}"',
        f'arxiv_id: "{safe_arxiv}"',
        f'updated_at: "{now}"',
        "---",
        "",
        "## Summary",
        summary or "(No summary extracted yet)",
        "",
        "## Notes",
        "",
        "## Q&A",
        "",
    ]
    note_path.write_text("\n".join(frontmatter), encoding="utf-8")
    return note_path


def append_qa_to_paper_note(config: PaperToolConfig, *, title: str, question: str, answer: str) -> Path:
    note_path = paper_note_path(config, title)
    if not note_path.exists():
        upsert_paper_note(
            config,
            title=title,
            source_path="",
            summary="",
            doi=None,
            arxiv_id=None,
        )
    now = datetime.now().isoformat(timespec="seconds")
    block = [
        f"### {now}",
        f"- Question: {question}",
        f"- Answer: {answer}",
        "",
    ]
    _append_markdown(note_path, "\n".join(block))
    return note_path


def append_qa_to_daily_note(
    config: PaperToolConfig,
    *,
    question: str,
    answer: str,
    paper_titles: list[str],
) -> Path:
    if not config.obsidian_vault:
        raise ValueError("obsidian_vault not configured")

    daily_dir = config.obsidian_vault / config.obsidian_daily_dir
    _ensure_dir(daily_dir)
    today = datetime.now().strftime("%Y-%m-%d")
    path = daily_dir / f"{today}.md"
    tags = ", ".join(paper_titles) if paper_titles else "(no source paper matched)"
    content = [
        "## PaperTool Q&A",
        f"- Papers: {tags}",
        f"- Question: {question}",
        f"- Answer: {answer}",
        "",
    ]
    _append_markdown(path, "\n".join(content))
    return path
