from __future__ import annotations

import random
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from papertool.db import PaperDB
from papertool.models import QuizQuestion

QUEUE_STATUSES = {"inbox", "today", "next", "later", "done"}


def validate_queue_status(status: str) -> str:
    value = status.strip().lower()
    if value not in QUEUE_STATUSES:
        allowed = ", ".join(sorted(QUEUE_STATUSES))
        raise ValueError(f"Invalid queue status '{status}'. Allowed: {allowed}")
    return value


def queue_rows_to_dict(rows: list[Any]) -> list[dict[str, object]]:
    payload: list[dict[str, object]] = []
    for row in rows:
        payload.append(
            {
                "paper_id": row["paper_id"],
                "status": row["status"],
                "priority": row["priority"],
                "title": row["title"],
                "path": row["path"],
                "ingested_at": row["ingested_at"],
                "last_planned_for": row["last_planned_for"],
                "completed_at": row["completed_at"] if "completed_at" in row.keys() else None,
            }
        )
    return payload


def _extract_seed(summary: str, full_text: str) -> str:
    if summary.strip():
        return summary.strip()
    sentences = re.split(r"(?<=[.!?])\s+", full_text.strip())
    for sentence in sentences:
        clean = sentence.strip()
        if len(clean) > 30:
            return clean[:260]
    return full_text[:260].strip() or "this paper"


def _micro_templates(title: str, seed: str) -> list[tuple[str, str]]:
    trimmed = seed[:220]
    return [
        (
            f"In one or two sentences, what problem does '{title}' solve?",
            trimmed,
        ),
        (
            f"What is the key method or mechanism in '{title}'?",
            trimmed,
        ),
        (
            f"What result or implication from '{title}' do you want to remember tomorrow?",
            trimmed,
        ),
        (
            f"Explain this statement from '{title}': {trimmed}",
            trimmed,
        ),
        (
            f"If you had to teach '{title}' quickly, what are the two most important points?",
            trimmed,
        ),
    ]


def generate_micro_quiz_for_paper(db: PaperDB, paper_id: str, count: int = 3) -> list[QuizQuestion]:
    paper = db.get_paper(paper_id)
    if not paper:
        return []

    count = max(1, min(count, 5))
    now = datetime.now(timezone.utc)
    seed = _extract_seed(paper["summary"] or "", paper["full_text"] or "")
    templates = _micro_templates(paper["title"], seed)
    selected = random.sample(templates, k=min(count, len(templates)))

    questions: list[QuizQuestion] = []
    for prompt, expected in selected:
        qid = str(uuid.uuid4())
        question = QuizQuestion(
            question_id=qid,
            paper_id=paper["id"],
            paper_title=paper["title"],
            prompt=prompt,
            expected_answer=expected,
            created_at=now,
        )
        db.save_quiz_question(
            question_id=question.question_id,
            paper_id=question.paper_id,
            question_text=question.prompt,
            expected_answer=question.expected_answer,
            source="micro",
        )
        questions.append(question)
    return questions


def due_review_questions(db: PaperDB, count: int = 5) -> list[QuizQuestion]:
    rows = db.due_review_cards(limit=count)
    now = datetime.now(timezone.utc)
    questions: list[QuizQuestion] = []

    for row in rows:
        qid = str(uuid.uuid4())
        question = QuizQuestion(
            question_id=qid,
            paper_id=row["paper_id"],
            paper_title=row["paper_title"],
            prompt=row["question_text"],
            expected_answer=row["expected_answer"],
            created_at=now,
        )
        db.save_quiz_question(
            question_id=question.question_id,
            paper_id=question.paper_id,
            question_text=question.prompt,
            expected_answer=question.expected_answer,
            source="review",
        )
        questions.append(question)

    return questions


def paper_of_day_payload(db: PaperDB) -> dict[str, object] | None:
    row = db.paper_of_day()
    if not row:
        return None
    return {
        "paper_id": row["paper_id"],
        "title": row["title"],
        "path": row["path"],
        "status": row["status"],
        "priority": row["priority"],
        "ingested_at": row["ingested_at"],
    }
