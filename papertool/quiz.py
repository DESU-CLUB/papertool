from __future__ import annotations

import random
import re
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from papertool.db import PaperDB
from papertool.models import QuizQuestion
from papertool.retrieval import rank_quiz_papers


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def recency_weight(ingested_at: str, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    ingested = _parse_iso(ingested_at) or now
    days_old = max((now - ingested).total_seconds() / 86400.0, 0.0)
    return 1.0 / (1.0 + days_old / 10.0)


def _select_weighted_without_replacement(items: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    pool = items[:]

    while pool and len(selected) < count:
        total = sum(max(float(item["weight"]), 0.0001) for item in pool)
        pick = random.random() * total
        running = 0.0
        index = 0
        for index, item in enumerate(pool):
            running += max(float(item["weight"]), 0.0001)
            if running >= pick:
                break
        selected.append(pool.pop(index))
    return selected


def _extract_prompt_seed(summary: str, full_text: str) -> str:
    if summary.strip():
        return summary.strip()
    sentences = re.split(r"(?<=[.!?])\s+", full_text.strip())
    for sentence in sentences:
        clean = sentence.strip()
        if len(clean) > 30:
            return clean[:260]
    return full_text[:260].strip() or "this paper"


def _question_templates(title: str, seed: str) -> list[tuple[str, str]]:
    trimmed = seed[:220]
    return [
        (
            f"What is the central idea of '{title}'?",
            trimmed,
        ),
        (
            f"Explain why this statement matters in '{title}': {trimmed}",
            trimmed,
        ),
        (
            f"How would you teach the key concept from '{title}' to someone else?",
            trimmed,
        ),
    ]


def _compute_mix_targets(total_count: int, recycled_available: int) -> tuple[int, int]:
    if total_count <= 0:
        return 0, 0
    recycled_target = min(recycled_available, total_count // 5)
    new_target = max(total_count - recycled_target, 0)
    return new_target, recycled_target


def _new_question_from_paper(paper: Any, now: datetime) -> QuizQuestion:
    seed = _extract_prompt_seed(paper["summary"] or "", paper["full_text"] or "")
    prompt, expected = random.choice(_question_templates(paper["title"], seed))
    return QuizQuestion(
        question_id=str(uuid.uuid4()),
        paper_id=paper["id"],
        paper_title=paper["title"],
        prompt=prompt,
        expected_answer=expected,
        created_at=now,
    )


def _recycled_question_from_row(row: Any, now: datetime) -> QuizQuestion:
    return QuizQuestion(
        question_id=str(uuid.uuid4()),
        paper_id=row["paper_id"],
        paper_title=row["paper_title"],
        prompt=row["question_text"],
        expected_answer=row["expected_answer"],
        created_at=now,
    )


def generate_daily_quiz(db: PaperDB, count: int = 5) -> list[QuizQuestion]:
    if count <= 0:
        return []
    now = datetime.now(timezone.utc)
    rows = db.paper_activity()
    recycled_rows = db.wrong_question_pool(limit=max(100, count * 5))
    if not rows and not recycled_rows:
        return []

    ranked_candidates = rank_quiz_papers(
        db,
        count=max(count * 5, 25),
        include_queue_boost=True,
        diversify_by_topic=True,
    )
    activity_by_id = {str(row["id"]): row for row in rows}

    candidates: list[dict[str, Any]] = []
    ranked_ids: list[str] = [str(item["paper_id"]) for item in ranked_candidates]
    if not ranked_ids:
        ranked_ids = list(activity_by_id.keys())

    for paper_id in ranked_ids:
        row = activity_by_id.get(paper_id)
        if not row:
            continue
        paper = db.get_paper(paper_id)
        if not paper:
            continue
        ranked_score = next(
            (float(item["score"]) for item in ranked_candidates if str(item["paper_id"]) == paper_id),
            recency_weight(str(row["ingested_at"]), now=now),
        )
        base_weight = max(ranked_score, 0.0001)
        quiz_count = int(row["quiz_count"] or 0)
        score = float(row["avg_score"] or 0.0)

        novelty_boost = 1.25 if quiz_count == 0 else 1.0
        weak_area_boost = 1.2 if quiz_count > 0 and score < 0.7 else 1.0
        weight = base_weight * novelty_boost * weak_area_boost

        candidates.append(
            {
                "paper": paper,
                "weight": weight,
            }
        )

    new_target, recycled_target = _compute_mix_targets(count, len(recycled_rows))
    if recycled_target < count and not candidates:
        recycled_target = min(len(recycled_rows), count)
        new_target = count - recycled_target

    chosen = _select_weighted_without_replacement(candidates, new_target) if new_target > 0 else []
    questions: list[QuizQuestion] = []

    # Recycled questions are prepended as explicit retries from previous incorrect attempts.
    for row in recycled_rows[:recycled_target]:
        question = _recycled_question_from_row(row, now=now)
        db.save_quiz_question(
            question_id=question.question_id,
            paper_id=question.paper_id,
            question_text=question.prompt,
            expected_answer=question.expected_answer,
            source="recycled",
        )
        questions.append(question)

    for candidate in chosen:
        question = _new_question_from_paper(candidate["paper"], now=now)
        db.save_quiz_question(
            question_id=question.question_id,
            paper_id=question.paper_id,
            question_text=question.prompt,
            expected_answer=question.expected_answer,
            source="daily",
        )
        questions.append(question)

    # Backfill to requested batch size if weighted selection could not fill all slots.
    if len(questions) < count:
        remaining = count - len(questions)
        extra_recycled = recycled_rows[recycled_target : recycled_target + remaining]
        for row in extra_recycled:
            question = _recycled_question_from_row(row, now=now)
            db.save_quiz_question(
                question_id=question.question_id,
                paper_id=question.paper_id,
                question_text=question.prompt,
                expected_answer=question.expected_answer,
                source="recycled",
            )
            questions.append(question)

    if len(questions) < count and candidates:
        while len(questions) < count:
            candidate = random.choices(
                candidates,
                weights=[max(float(item["weight"]), 0.0001) for item in candidates],
                k=1,
            )[0]
            question = _new_question_from_paper(candidate["paper"], now=now)
            db.save_quiz_question(
                question_id=question.question_id,
                paper_id=question.paper_id,
                question_text=question.prompt,
                expected_answer=question.expected_answer,
                source="daily",
            )
            questions.append(question)

    return questions


def quiz_to_dict(questions: list[QuizQuestion]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for question in questions:
        item = asdict(question)
        item["created_at"] = question.created_at.isoformat()
        payload.append(item)
    return payload
