from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from papertool.citations import extract_cited_identifiers, find_identifiers, link_citations
from papertool.db import PaperDB
from papertool.models import PaperRecord

SUPPORTED_SUFFIXES = {".pdf", ".md", ".txt"}


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
    if text:
        for line in text.splitlines()[:30]:
            clean = line.strip()
            if len(clean) >= 15 and not clean.lower().startswith("arxiv"):
                return clean[:220]
    return path.stem.replace("_", " ").replace("-", " ")


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


def ingest_folder(db: PaperDB, folder: Path) -> IngestStats:
    stats = IngestStats()
    files = scan_papers(folder)
    stats.scanned = len(files)

    for path in files:
        try:
            stats = ingest_file(db, path, stats)
        except Exception:
            stats.skipped += 1
    rebuild_citation_graph(db)
    return stats


def ingest_file(db: PaperDB, path: Path, stats: IngestStats | None = None) -> IngestStats:
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
    stats.ingested += 1
    return stats


def rebuild_citation_graph(db: PaperDB) -> None:
    papers = db.list_papers()
    doi_to_paper: dict[str, str] = {}
    arxiv_to_paper: dict[str, str] = {}

    for paper in papers:
        if paper["doi"]:
            doi_to_paper[paper["doi"].lower()] = paper["id"]
        if paper["arxiv_id"]:
            arxiv_to_paper[paper["arxiv_id"].lower()] = paper["id"]

    for paper in papers:
        row = db.get_paper(paper["id"])
        if not row:
            continue
        cited = extract_cited_identifiers(row["full_text"] or "")
        links = link_citations(cited, doi_to_paper=doi_to_paper, arxiv_to_paper=arxiv_to_paper)
        db.set_citations(paper["id"], links)


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
