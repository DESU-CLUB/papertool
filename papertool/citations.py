from __future__ import annotations

import re
from collections.abc import Iterable

DOI_PATTERN = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")
ARXIV_PATTERN = re.compile(r"(?:arXiv:)?(\d{4}\.\d{4,5}(?:v\d+)?)", re.IGNORECASE)


def normalize_doi(doi: str) -> str:
    return doi.strip().lower().rstrip(".")


def normalize_arxiv(arxiv_id: str) -> str:
    return arxiv_id.strip().lower().replace("arxiv:", "")


def find_identifiers(text: str) -> tuple[set[str], set[str]]:
    dois = {normalize_doi(m.group(0)) for m in DOI_PATTERN.finditer(text)}
    arxivs = {normalize_arxiv(m.group(1)) for m in ARXIV_PATTERN.finditer(text)}
    return dois, arxivs


def extract_reference_section(text: str) -> str:
    if not text:
        return ""
    lower = text.lower()
    start = max(len(text) // 2, 0)
    idx = lower.find("references", start)
    if idx == -1:
        idx = lower.find("bibliography", start)
    if idx == -1:
        return text[-15000:]
    return text[idx:]


def extract_cited_identifiers(text: str) -> tuple[set[str], set[str]]:
    references = extract_reference_section(text)
    return find_identifiers(references)


def link_citations(
    source_identifiers: tuple[set[str], set[str]],
    doi_to_paper: dict[str, str],
    arxiv_to_paper: dict[str, str],
) -> list[tuple[str, str, float]]:
    links: list[tuple[str, str, float]] = []
    dois, arxivs = source_identifiers

    for doi in dois:
        target = doi_to_paper.get(normalize_doi(doi))
        if target:
            links.append((target, f"doi:{doi}", 0.95))

    for arxiv in arxivs:
        target = arxiv_to_paper.get(normalize_arxiv(arxiv))
        if target:
            links.append((target, f"arxiv:{arxiv}", 0.9))

    seen: set[str] = set()
    unique: list[tuple[str, str, float]] = []
    for target, reason, confidence in links:
        if target in seen:
            continue
        seen.add(target)
        unique.append((target, reason, confidence))
    return unique


def join_nonempty(values: Iterable[str | None], sep: str = " ") -> str:
    return sep.join(v for v in values if v)
