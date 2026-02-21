from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from papertool.citations import (
    derive_local_title_variants,
    build_reference_preview,
    extract_cited_identifiers,
    extract_reference_candidates,
    find_identifiers,
    link_citations,
    match_reference_titles_to_local_papers,
    normalize_arxiv,
    normalize_doi,
)
from papertool.config import PaperToolConfig, load_config
from papertool.db import PaperDB
from papertool.models import PaperRecord

SUPPORTED_SUFFIXES = {".pdf", ".md", ".txt"}
_TITLE_SKIP_PREFIXES = (
    "arxiv:",
    "published as",
    "accepted at",
    "accepted to",
    "under review",
    "preprint",
    "camera ready",
    "this version",
    "code:",
    "abstract",
)


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class IngestStats:
    scanned: int = 0
    ingested: int = 0
    skipped: int = 0


def scan_papers(folder: Path) -> list[Path]:
    return [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES]


def extract_text(path: Path, max_chars: int = 180_000) -> str:
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt"}:
        return path.read_text(encoding="utf-8", errors="ignore")[:max_chars]

    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ModuleNotFoundError:
            return ""
        reader = PdfReader(str(path))
        pages: list[str] = []
        for page in reader.pages:
            pages.append(page.extract_text() or "")
            if sum(len(p) for p in pages) >= max_chars:
                break
        return "\n".join(pages)[:max_chars]

    return ""


def extract_title(path: Path, text: str) -> str:
    fallback = path.stem.replace("_", " ").replace("-", " ")
    if not text:
        return fallback

    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()[:80]]
    lines = [line for line in lines if line]
    for idx, line in enumerate(lines):
        if not _is_candidate_title_line(line):
            continue
        candidate = line
        if _looks_uppercase_title_line(line):
            parts = [line]
            cursor = idx + 1
            while cursor < len(lines) and len(parts) < 3:
                next_line = lines[cursor]
                if not _is_candidate_title_line(next_line):
                    break
                if not _looks_uppercase_title_line(next_line):
                    break
                parts.append(next_line)
                cursor += 1
            candidate = " ".join(parts)
        candidate = re.sub(r"\s*:\s*", ": ", candidate).strip()
        if len(candidate) >= 15:
            return candidate[:220]
    return fallback


def _is_candidate_title_line(line: str) -> bool:
    clean = line.strip()
    if len(clean) < 12:
        return False
    lower = clean.lower()
    if lower.startswith(_TITLE_SKIP_PREFIXES):
        return False
    if "@" in clean:
        return False
    if "http://" in lower or "https://" in lower:
        return False
    if re.match(r"^\d+(\.\d+)*\s", clean):
        return False
    token_count = len(re.findall(r"[A-Za-z0-9]+", clean))
    if token_count < 3:
        return False
    return True


def _looks_uppercase_title_line(line: str) -> bool:
    letters = [ch for ch in line if ch.isalpha()]
    if len(letters) < 8:
        return False
    upper = sum(1 for ch in letters if ch.isupper())
    return (upper / len(letters)) >= 0.8


def extract_summary(text: str) -> str:
    if not text:
        return ""
    paragraph = re.split(r"\n\s*\n", text.strip())[0]
    paragraph = re.sub(r"\s+", " ", paragraph).strip()
    if len(paragraph) > 500:
        paragraph = paragraph[:500].rsplit(" ", 1)[0] + "..."
    return paragraph


def extract_year(text: str) -> int | None:
    matches = re.findall(r"\b(19\d{2}|20[0-4]\d)\b", text[:6000])
    if not matches:
        return None
    return int(matches[0])


def split_chunks(text: str, chunk_size: int = 1200, overlap: int = 200) -> list[str]:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return []

    chunks: list[str] = []
    start = 0
    while start < len(cleaned):
        end = min(start + chunk_size, len(cleaned))
        chunk = cleaned[start:end]
        if end < len(cleaned):
            split_at = chunk.rfind(". ")
            if split_at > chunk_size // 3:
                end = start + split_at + 1
                chunk = cleaned[start:end]
        chunks.append(chunk.strip())
        if end >= len(cleaned):
            break
        start = max(end - overlap, start + 1)
    return chunks


def ingest_folder(db: PaperDB, folder: Path, config: PaperToolConfig | None = None) -> IngestStats:
    stats = IngestStats()
    files = scan_papers(folder)
    stats.scanned = len(files)
    touched_paper_ids: set[str] = set()

    for path in files:
        before_ingested = stats.ingested
        try:
            stats = ingest_file(db, path, stats, refresh_citations=False, config=config)
            if stats.ingested > before_ingested:
                row = db.get_paper_by_path(str(path.resolve()))
                if row:
                    touched_paper_ids.add(str(row["id"]))
        except Exception:
            stats.skipped += 1
    if touched_paper_ids:
        _maybe_refresh_citation_graph(db, paper_ids=list(touched_paper_ids), config=config)
    return stats


def ingest_file(
    db: PaperDB,
    path: Path,
    stats: IngestStats | None = None,
    *,
    refresh_citations: bool = True,
    config: PaperToolConfig | None = None,
) -> IngestStats:
    stats = stats or IngestStats(scanned=1)
    stat = path.stat()
    mtime = stat.st_mtime

    existing = db.get_paper_by_path(str(path.resolve()))
    if existing and abs(existing["mtime"] - mtime) < 0.00001:
        stats.skipped += 1
        return stats

    text = extract_text(path)
    title = extract_title(path, text)
    summary = extract_summary(text)
    doi_values, arxiv_values = find_identifiers(text[:12000])
    doi = next(iter(doi_values), None)
    arxiv_id = next(iter(arxiv_values), None)
    year = extract_year(text)

    paper = PaperRecord(
        id=_sha1(str(path.resolve())),
        title=title,
        path=str(path.resolve()),
        ingested_at=_utc_now(),
        mtime=mtime,
        doi=doi,
        arxiv_id=arxiv_id,
        published_date=str(year) if year is not None else None,
        summary=summary,
    )

    db.upsert_paper(paper, text)
    db.insert_chunks(paper.id, split_chunks(text))
    _maybe_refresh_retrieval_index(db, paper.id)
    if refresh_citations:
        _maybe_refresh_citation_graph(db, paper_ids=[paper.id], config=config)
    stats.ingested += 1
    return stats


def _maybe_refresh_retrieval_index(db: PaperDB, paper_id: str) -> None:
    # Index refresh is best-effort. Retrieval falls back to Python if Rust is unavailable.
    try:
        from papertool.rust_backend import build_index

        cfg = load_config()
        if cfg.db_path.resolve() != db.db_path.resolve():
            return
        if cfg.retrieval_backend not in {"shadow", "rust"}:
            return
        build_index(db, cfg.rust_index_dir, paper_id=paper_id)
    except Exception:
        return


def _citation_reason_priority(reason: str) -> int:
    value = (reason or "").lower()
    if value.startswith("doi:"):
        return 4
    if value.startswith("arxiv:"):
        return 3
    if value.startswith("openalex_ref:"):
        return 2
    if value.startswith("title:"):
        return 1
    return 0


def _merge_citation_links(links: list[tuple[str, str, float]]) -> list[tuple[str, str, float]]:
    best: dict[str, tuple[str, float]] = {}
    for target_id, reason, confidence in links:
        current = best.get(target_id)
        candidate_priority = _citation_reason_priority(reason)
        if current is None:
            best[target_id] = (reason, float(confidence))
            continue
        current_reason, current_confidence = current
        current_priority = _citation_reason_priority(current_reason)
        if candidate_priority > current_priority:
            best[target_id] = (reason, float(confidence))
            continue
        if candidate_priority == current_priority and float(confidence) > current_confidence:
            best[target_id] = (reason, float(confidence))
    merged = [(target_id, payload[0], float(payload[1])) for target_id, payload in best.items()]
    merged.sort(key=lambda row: (_citation_reason_priority(row[1]), row[2]), reverse=True)
    return merged


def _effective_citation_config(
    db: PaperDB,
    config: PaperToolConfig | None,
) -> PaperToolConfig:
    if config is not None:
        return config
    cfg = load_config()
    if cfg.db_path.resolve() == db.db_path.resolve():
        return cfg
    return replace(cfg, db_path=db.db_path.resolve())


def _maybe_refresh_citation_graph(
    db: PaperDB,
    *,
    paper_ids: list[str] | None = None,
    config: PaperToolConfig | None = None,
) -> None:
    try:
        if config is None:
            loaded = load_config()
            if loaded.db_path.resolve() != db.db_path.resolve():
                return
            cfg = loaded
        else:
            cfg = config
        if not bool(cfg.citation_refresh_on_import):
            return
        rebuild_citation_graph(
            db,
            paper_ids=paper_ids,
            config=cfg,
        )
    except Exception:
        return


def rebuild_citation_graph(
    db: PaperDB,
    paper_ids: list[str] | None = None,
    config: PaperToolConfig | None = None,
) -> dict[str, object]:
    cfg = _effective_citation_config(db, config)

    all_papers = db.papers_minimal_for_citation_match()
    requested_ids = [str(item).strip() for item in (paper_ids or []) if str(item).strip()]
    source_ids: set[str] = set()
    unresolved_ids: list[str] = []
    for raw_id in requested_ids:
        resolved = db.resolve_paper_id(raw_id)
        if resolved:
            source_ids.add(resolved)
        else:
            unresolved_ids.append(raw_id)
    source_papers = [paper for paper in all_papers if not source_ids or str(paper["id"]) in source_ids]
    if requested_ids and not source_papers:
        return {
            "ok": False,
            "processed": 0,
            "edges_set": 0,
            "requested": requested_ids,
            "resolved_ids": sorted(source_ids),
            "unresolved_ids": unresolved_ids,
            "error": "no_source_papers_resolved",
        }

    doi_to_paper: dict[str, str] = {}
    arxiv_to_paper: dict[str, str] = {}
    full_rows: dict[str, Any] = {}
    local_match_papers: list[dict[str, Any]] = []
    for paper in all_papers:
        paper_id = str(paper["id"])
        if paper["doi"]:
            doi_to_paper[normalize_doi(str(paper["doi"]))] = paper_id
        if paper["arxiv_id"]:
            arxiv_to_paper[normalize_arxiv(str(paper["arxiv_id"]))] = paper_id
        full = db.get_paper(paper_id)
        full_rows[paper_id] = full
        local_match_papers.append(
            {
                "id": paper_id,
                "title": str(paper["title"] or ""),
                "published_date": paper["published_date"],
                "title_variants": derive_local_title_variants(
                    str(paper["title"] or ""),
                    str((full["full_text"] if full else "") or ""),
                ),
            }
        )

    total_edges = 0
    for paper in source_papers:
        paper_id = str(paper["id"])
        row = full_rows.get(paper_id) or db.get_paper(paper_id)
        full_text = str(row["full_text"] or "") if row else ""
        links: list[tuple[str, str, float]] = []

        cited = extract_cited_identifiers(full_text)
        links.extend(link_citations(cited, doi_to_paper=doi_to_paper, arxiv_to_paper=arxiv_to_paper))
        refs = extract_reference_candidates(full_text)
        links.extend(
            match_reference_titles_to_local_papers(
                refs,
                local_papers=local_match_papers,
                mode=cfg.citation_title_match_mode,
                source_paper_id=paper_id,
            )
        )

        merged = _merge_citation_links([link for link in links if link[0] != paper_id])
        db.set_citations(paper_id, merged)
        total_edges += len(merged)

    return {
        "ok": True,
        "processed": len(source_papers),
        "edges_set": total_edges,
        "requested": requested_ids,
        "resolved_ids": sorted(source_ids),
        "unresolved_ids": unresolved_ids,
    }


def citation_mentions_preview(
    db: PaperDB,
    *,
    paper_id: str,
    mode: str = "conservative",
    limit: int = 80,
) -> list[dict[str, object]]:
    row = db.get_paper(paper_id)
    if not row:
        return []
    all_papers = db.papers_minimal_for_citation_match()
    local_match_papers: list[dict[str, Any]] = []
    for paper in all_papers:
        target_id = str(paper["id"])
        full = db.get_paper(target_id)
        local_match_papers.append(
            {
                "id": target_id,
                "title": str(paper["title"] or ""),
                "published_date": paper["published_date"],
                "doi": paper["doi"],
                "arxiv_id": paper["arxiv_id"],
                "title_variants": derive_local_title_variants(
                    str(paper["title"] or ""),
                    str((full["full_text"] if full else "") or ""),
                ),
            }
        )

    refs = extract_reference_candidates(str(row["full_text"] or ""))
    return build_reference_preview(
        refs,
        local_papers=local_match_papers,
        source_paper_id=paper_id,
        mode=mode,
        limit=max(1, int(limit)),
    )


def build_graph_payload(db: PaperDB) -> dict[str, list[dict[str, object]]]:
    papers = db.list_papers()
    edges = db.citation_edges()

    nodes = [
        {
            "id": p["id"],
            "title": p["title"],
            "path": p["path"],
            "doi": p["doi"],
            "arxiv_id": p["arxiv_id"],
            "published_date": p["published_date"],
            "ingested_at": p["ingested_at"],
        }
        for p in papers
    ]

    links = [
        {
            "source": e["source_paper_id"],
            "target": e["target_paper_id"],
            "reason": e["reason"],
            "confidence": e["confidence"],
            "source_title": e["source_title"],
            "target_title": e["target_title"],
        }
        for e in edges
    ]
    return {"nodes": nodes, "links": links}
