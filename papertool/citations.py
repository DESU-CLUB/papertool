from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

DOI_PATTERN = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")
ARXIV_PATTERN = re.compile(r"(?:arXiv:)?(\d{4}\.\d{4,5}(?:v\d+)?)", re.IGNORECASE)
YEAR_PATTERN = re.compile(r"\b(19\d{2}|20[0-4]\d)\b")
ENTRY_START_PATTERN = re.compile(r"^\s*(\[\d+\]|\(\d+\)|\d+[.)])\s+")
TITLE_STOPWORDS = {
    "a",
    "an",
    "and",
    "for",
    "in",
    "of",
    "on",
    "the",
    "to",
    "with",
}


@dataclass(slots=True)
class ReferenceCandidate:
    raw_entry: str
    title: str
    year: int | None
    dois: set[str]
    arxiv_ids: set[str]


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


def extract_reference_entries(text: str) -> list[str]:
    section = extract_reference_section(text)
    lines = [re.sub(r"\s+", " ", line).strip() for line in section.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return []
    first_numbered = next((idx for idx, line in enumerate(lines) if ENTRY_START_PATTERN.match(line)), None)
    if first_numbered is not None:
        lines = lines[first_numbered:]
    else:
        lines = [line for line in lines if line.lower() not in {"references", "bibliography"}]
    if not lines:
        return []

    entries: list[str] = []
    buffer: list[str] = []
    for line in lines:
        is_new = bool(ENTRY_START_PATTERN.match(line))
        if is_new and buffer:
            entries.append(" ".join(buffer).strip())
            buffer = [line]
            continue
        buffer.append(line)
    if buffer:
        entries.append(" ".join(buffer).strip())

    return [entry for entry in entries if len(entry) > 20]


def _extract_title_from_reference(entry: str) -> str:
    text = re.sub(r"^\s*(\[\d+\]|\(\d+\)|\d+[.)])\s*", "", entry).strip()
    text = DOI_PATTERN.sub(" ", text)
    text = ARXIV_PATTERN.sub(" ", text)
    quoted = re.findall(r"[\"“](.+?)[\"”]", text)
    if quoted:
        return max((item.strip() for item in quoted), key=len, default="")

    pieces = [piece.strip(" .;:") for piece in re.split(r"\.\s+", text) if piece.strip()]
    best = ""
    best_score = -100.0
    for piece in pieces:
        tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-']*", piece)
        if len(tokens) < 4:
            continue
        lower = piece.lower()
        score = float(len(tokens))
        if ":" in piece:
            score += 3.0
        if piece.count(",") >= 3:
            score -= 5.0
        if lower.startswith(("in ", "proc", "proceedings", "journal", "arxiv", "doi", "http")):
            score -= 6.0
        if any(token.lower() in TITLE_STOPWORDS for token in tokens):
            score += 1.0
        if score > best_score:
            best_score = score
            best = piece
    return best.strip()


def derive_local_title_variants(title: str, full_text: str) -> list[str]:
    variants: list[str] = []
    if title.strip():
        variants.append(title.strip())
    lines = [re.sub(r"\s+", " ", line).strip() for line in (full_text or "").splitlines()[:24]]
    lines = [line for line in lines if line]
    if lines:
        variants.append(lines[0])
    if len(lines) >= 2:
        first = lines[0]
        second = lines[1]
        if (
            first.endswith(":")
            or len(first.split()) <= 8
            or second[:1].islower()
            or second.lower().startswith(("with ", "for ", "and "))
        ):
            variants.append(f"{first} {second}")
    unique: list[str] = []
    seen: set[str] = set()
    for value in variants:
        key = normalize_title_for_match(value)
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(value)
    return unique


def extract_reference_candidates(text: str) -> list[ReferenceCandidate]:
    candidates: list[ReferenceCandidate] = []
    for entry in extract_reference_entries(text):
        dois, arxivs = find_identifiers(entry)
        year_match = YEAR_PATTERN.search(entry)
        title = _extract_title_from_reference(entry)
        candidates.append(
            ReferenceCandidate(
                raw_entry=entry,
                title=title,
                year=int(year_match.group(1)) if year_match else None,
                dois=dois,
                arxiv_ids=arxivs,
            )
        )
    return candidates


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


def normalize_title_for_match(title: str) -> str:
    if not title:
        return ""
    value = unicodedata.normalize("NFKD", title)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _token_set(value: str) -> set[str]:
    return {token for token in value.split() if token}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _extract_year(value: Any) -> int | None:
    if value is None:
        return None
    match = YEAR_PATTERN.search(str(value))
    if not match:
        return None
    return int(match.group(1))


def _match_mode_params(mode: str) -> tuple[float, float, float, float, float]:
    match_mode = (mode or "conservative").strip().lower()
    if match_mode == "aggressive":
        return 0.87, 0.84, 0.60, 0.74, 0.66
    if match_mode == "balanced":
        return 0.90, 0.87, 0.66, 0.78, 0.70
    return 0.93, 0.90, 0.72, 0.82, 0.74


def match_reference_titles_to_local_papers(
    candidates: list[ReferenceCandidate],
    local_papers: list[Any],
    mode: str = "conservative",
    source_paper_id: str | None = None,
) -> list[tuple[str, str, float]]:
    ratio_hi, ratio_lo, jaccard_min, conf_hi, conf_med = _match_mode_params(mode)

    local_index: list[dict[str, Any]] = []
    for paper in local_papers:
        paper_id = str(paper["id"])
        variants = paper.get("title_variants") if isinstance(paper, dict) else None
        if not isinstance(variants, list) or not variants:
            variants = [str(paper["title"] or "")]
        year = _extract_year(paper["published_date"])
        for variant in variants:
            title_norm = normalize_title_for_match(str(variant or ""))
            if not title_norm:
                continue
            local_index.append(
                {
                    "paper_id": paper_id,
                    "title_norm": title_norm,
                    "tokens": _token_set(title_norm),
                    "year": year,
                }
            )

    best_by_target: dict[str, tuple[str, float, float, float]] = {}
    for candidate in candidates:
        cand_norm = normalize_title_for_match(candidate.title)
        if len(cand_norm) < 12:
            continue
        cand_tokens = _token_set(cand_norm)
        if len(cand_tokens) < 3:
            continue
        for paper in local_index:
            target_id = str(paper["paper_id"])
            if source_paper_id and source_paper_id == target_id:
                continue
            ratio = SequenceMatcher(None, cand_norm, str(paper["title_norm"])).ratio()
            jaccard = _jaccard(cand_tokens, paper["tokens"])
            year_ok = True
            if candidate.year is not None and paper["year"] is not None:
                year_ok = abs(candidate.year - int(paper["year"])) <= 1
            if not year_ok:
                continue
            is_hi = ratio >= ratio_hi
            is_med = ratio >= ratio_lo and jaccard >= jaccard_min
            if not (is_hi or is_med):
                continue
            confidence = conf_hi if is_hi else conf_med
            current = best_by_target.get(target_id)
            if current and current[1] >= confidence:
                continue
            best_by_target[target_id] = (cand_norm, confidence, ratio, jaccard)

    ordered = sorted(
        ((target_id, payload) for target_id, payload in best_by_target.items()),
        key=lambda item: (item[1][1], item[1][2], item[1][3]),
        reverse=True,
    )
    return [(target_id, f"title:{payload[0]}", float(payload[1])) for target_id, payload in ordered]


def build_reference_preview(
    candidates: list[ReferenceCandidate],
    local_papers: list[Any],
    *,
    source_paper_id: str | None = None,
    mode: str = "conservative",
    limit: int = 80,
) -> list[dict[str, object]]:
    ratio_hi, ratio_lo, jaccard_min, conf_hi, conf_med = _match_mode_params(mode)
    local_index: list[dict[str, Any]] = []
    title_by_id: dict[str, str] = {}
    doi_to_paper: dict[str, str] = {}
    arxiv_to_paper: dict[str, str] = {}
    for paper in local_papers:
        paper_id = str(paper["id"])
        title_by_id[paper_id] = str(paper["title"] or "")
        if paper.get("doi"):
            doi_to_paper[normalize_doi(str(paper["doi"]))] = paper_id
        if paper.get("arxiv_id"):
            arxiv_to_paper[normalize_arxiv(str(paper["arxiv_id"]))] = paper_id
        variants = paper.get("title_variants") if isinstance(paper, dict) else None
        if not isinstance(variants, list) or not variants:
            variants = [str(paper["title"] or "")]
        year = _extract_year(paper["published_date"])
        for variant in variants:
            title_norm = normalize_title_for_match(str(variant or ""))
            if not title_norm:
                continue
            local_index.append(
                {
                    "paper_id": paper_id,
                    "title_norm": title_norm,
                    "tokens": _token_set(title_norm),
                    "year": year,
                }
            )

    previews: list[dict[str, object]] = []
    for idx, candidate in enumerate(candidates[: max(1, int(limit))]):
        matched_id: str | None = None
        reason: str | None = None
        confidence: float | None = None
        method: str | None = None

        for doi in sorted(candidate.dois):
            target_id = doi_to_paper.get(normalize_doi(doi))
            if target_id and (not source_paper_id or source_paper_id != target_id):
                matched_id = target_id
                reason = f"doi:{normalize_doi(doi)}"
                confidence = 0.95
                method = "doi"
                break

        if matched_id is None:
            for arxiv_id in sorted(candidate.arxiv_ids):
                target_id = arxiv_to_paper.get(normalize_arxiv(arxiv_id))
                if target_id and (not source_paper_id or source_paper_id != target_id):
                    matched_id = target_id
                    reason = f"arxiv:{normalize_arxiv(arxiv_id)}"
                    confidence = 0.9
                    method = "arxiv"
                    break

        if matched_id is None:
            cand_norm = normalize_title_for_match(candidate.title)
            if len(cand_norm) >= 12:
                cand_tokens = _token_set(cand_norm)
                if len(cand_tokens) >= 3:
                    best: tuple[str, float, float, float] | None = None
                    for paper in local_index:
                        target_id = str(paper["paper_id"])
                        if source_paper_id and source_paper_id == target_id:
                            continue
                        ratio = SequenceMatcher(None, cand_norm, str(paper["title_norm"])).ratio()
                        jaccard = _jaccard(cand_tokens, paper["tokens"])
                        year_ok = True
                        if candidate.year is not None and paper["year"] is not None:
                            year_ok = abs(candidate.year - int(paper["year"])) <= 1
                        if not year_ok:
                            continue
                        is_hi = ratio >= ratio_hi
                        is_med = ratio >= ratio_lo and jaccard >= jaccard_min
                        if not (is_hi or is_med):
                            continue
                        cand_conf = conf_hi if is_hi else conf_med
                        if best is None or cand_conf > best[1] or (cand_conf == best[1] and ratio > best[2]):
                            best = (target_id, cand_conf, ratio, jaccard)
                    if best is not None:
                        matched_id = best[0]
                        reason = f"title:{cand_norm}"
                        confidence = float(best[1])
                        method = "title"

        previews.append(
            {
                "mention_index": idx,
                "raw_entry": candidate.raw_entry,
                "extracted_title": candidate.title,
                "year_hint": candidate.year,
                "dois": sorted(candidate.dois),
                "arxiv_ids": sorted(candidate.arxiv_ids),
                "best_match": (
                    {
                        "target_paper_id": matched_id,
                        "target_title": title_by_id.get(matched_id or "", ""),
                        "reason": reason,
                        "confidence": confidence,
                        "method": method,
                    }
                    if matched_id and reason and confidence is not None and method
                    else None
                ),
            }
        )
    return previews


def join_nonempty(values: Iterable[str | None], sep: str = " ") -> str:
    return sep.join(v for v in values if v)
