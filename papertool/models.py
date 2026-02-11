from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class PaperRecord:
    id: str
    title: str
    path: str
    ingested_at: str
    mtime: float
    doi: str | None = None
    arxiv_id: str | None = None
    published_date: str | None = None
    summary: str | None = None


@dataclass(slots=True)
class SearchHit:
    paper_id: str
    title: str
    path: str
    snippet: str
    score: float


@dataclass(slots=True)
class QuizQuestion:
    question_id: str
    paper_id: str
    paper_title: str
    prompt: str
    expected_answer: str
    created_at: datetime
