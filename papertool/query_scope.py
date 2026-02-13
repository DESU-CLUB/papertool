from __future__ import annotations

from dataclasses import asdict, dataclass
import re

from papertool.db import PaperDB


STOP_WORDS = {
    "the",
    "a",
    "an",
    "of",
    "to",
    "and",
    "or",
    "for",
    "in",
    "on",
    "at",
    "is",
    "are",
    "be",
    "from",
    "with",
    "what",
    "how",
    "why",
    "when",
    "explain",
    "describe",
    "summarize",
    "about",
    "this",
    "that",
}

ARXIV_RE = re.compile(r"(?:arxiv:)?(\d{4}\.\d{4,5}(?:v\d+)?)", re.IGNORECASE)


@dataclass(slots=True)
class ScopeCandidate:
    paper_id: str
    title: str
    score: float
    reason: str


@dataclass(slots=True)
class ScopeResolution:
    selected_paper_ids: list[str]
    selected_papers: list[ScopeCandidate]
    candidates: list[ScopeCandidate]
    ambiguous: bool
    message: str

    def to_dict(self) -> dict[str, object]:
        return {
            "selected_paper_ids": list(self.selected_paper_ids),
            "selected_papers": [asdict(item) for item in self.selected_papers],
            "candidates": [asdict(item) for item in self.candidates],
            "ambiguous": self.ambiguous,
            "message": self.message,
        }


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _question_tokens(question: str) -> list[str]:
    raw = re.findall(r"[a-z0-9]+", question.lower())
    return [token for token in raw if len(token) > 2 and token not in STOP_WORDS]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _from_explicit_ids(db: PaperDB, paper_ids: list[str]) -> list[ScopeCandidate]:
    picked: list[ScopeCandidate] = []
    for paper_id in _dedupe(paper_ids):
        row = db.get_paper(paper_id)
        if not row:
            continue
        picked.append(
            ScopeCandidate(
                paper_id=str(row["id"]),
                title=str(row["title"]),
                score=1.0,
                reason="explicit_paper_id",
            )
        )
    return picked


def _from_arxiv_ids(db: PaperDB, arxiv_ids: list[str], reason: str) -> list[ScopeCandidate]:
    picked: list[ScopeCandidate] = []
    for arxiv_id in _dedupe(arxiv_ids):
        row = db.get_paper_by_arxiv_id(arxiv_id)
        if not row:
            continue
        picked.append(
            ScopeCandidate(
                paper_id=str(row["id"]),
                title=str(row["title"]),
                score=1.0,
                reason=reason,
            )
        )
    return picked


def resolve_question_scope(
    db: PaperDB,
    question: str,
    *,
    explicit_paper_ids: list[str] | None = None,
    explicit_arxiv_ids: list[str] | None = None,
    candidate_limit: int = 8,
) -> ScopeResolution:
    explicit_paper_ids = explicit_paper_ids or []
    explicit_arxiv_ids = explicit_arxiv_ids or []

    selected_candidates = _from_explicit_ids(db, explicit_paper_ids)
    selected_candidates.extend(_from_arxiv_ids(db, explicit_arxiv_ids, reason="explicit_arxiv_id"))
    selected_ids = _dedupe([item.paper_id for item in selected_candidates])
    if selected_ids:
        selected = []
        seen: set[str] = set()
        for item in selected_candidates:
            if item.paper_id in seen:
                continue
            seen.add(item.paper_id)
            selected.append(item)
        return ScopeResolution(
            selected_paper_ids=selected_ids,
            selected_papers=selected,
            candidates=selected,
            ambiguous=False,
            message="Using explicitly selected papers.",
        )

    extracted_arxiv = ARXIV_RE.findall(question or "")
    auto_arxiv = _from_arxiv_ids(db, extracted_arxiv, reason="question_arxiv_id")
    if auto_arxiv:
        resolved_ids = _dedupe([item.paper_id for item in auto_arxiv])
        unique_rows: list[ScopeCandidate] = []
        seen_ids: set[str] = set()
        for item in auto_arxiv:
            if item.paper_id in seen_ids:
                continue
            seen_ids.add(item.paper_id)
            unique_rows.append(item)
        return ScopeResolution(
            selected_paper_ids=resolved_ids,
            selected_papers=unique_rows,
            candidates=unique_rows[:candidate_limit],
            ambiguous=False,
            message="Matched paper(s) from arXiv IDs in the question.",
        )

    tokens = _question_tokens(question)
    if not tokens:
        return ScopeResolution(
            selected_paper_ids=[],
            selected_papers=[],
            candidates=[],
            ambiguous=True,
            message="Could not infer target papers. Pass --paper-id or --arxiv-id.",
        )

    title_rows = db.search_papers_by_title_tokens(tokens, limit=max(20, candidate_limit * 3))
    scored: list[ScopeCandidate] = []
    for row in title_rows:
        title = str(row["title"])
        title_compact = _compact(title)
        if not title_compact:
            continue
        matched = sum(1 for token in tokens if token in title_compact)
        overlap = matched / max(len(tokens), 1)
        phrase_bonus = 0.0
        question_compact = _compact(question)
        if question_compact and title_compact in question_compact:
            phrase_bonus = 0.2
        elif question_compact and question_compact in title_compact:
            phrase_bonus = 0.15
        score = min(1.0, overlap + phrase_bonus)
        if score < 0.34:
            continue
        scored.append(
            ScopeCandidate(
                paper_id=str(row["id"]),
                title=title,
                score=float(score),
                reason="title_token_match",
            )
        )
    scored.sort(key=lambda item: item.score, reverse=True)
    candidates = scored[:candidate_limit]

    selected = [item for item in candidates if item.score >= 0.55]
    if not selected and candidates:
        best = candidates[0]
        second = candidates[1] if len(candidates) > 1 else None
        if best.score >= 0.5 and (second is None or (best.score - second.score) >= 0.15):
            selected = [best]

    if not selected:
        return ScopeResolution(
            selected_paper_ids=[],
            selected_papers=[],
            candidates=candidates,
            ambiguous=True,
            message="Could not confidently map your question to paper IDs.",
        )

    if len(selected) > 5:
        return ScopeResolution(
            selected_paper_ids=[],
            selected_papers=[],
            candidates=candidates,
            ambiguous=True,
            message="Question maps to too many papers; narrow scope with --paper-id/--arxiv-id.",
        )

    return ScopeResolution(
        selected_paper_ids=[item.paper_id for item in selected],
        selected_papers=selected,
        candidates=candidates,
        ambiguous=False,
        message="Resolved scoped papers from title matching.",
    )
