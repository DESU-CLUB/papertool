from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile

from papertool.db import PaperDB
from papertool.models import PaperRecord
from papertool.quiz import recency_weight
from papertool.quiz import _compute_mix_targets, generate_daily_quiz


def test_recency_weight_prefers_recent_items() -> None:
    now = datetime(2026, 2, 11, tzinfo=timezone.utc)
    recent = (now - timedelta(days=2)).isoformat()
    older = (now - timedelta(days=100)).isoformat()

    assert recency_weight(recent, now=now) > recency_weight(older, now=now)


def test_recency_weight_non_negative() -> None:
    now = datetime(2026, 2, 11, tzinfo=timezone.utc)
    future = (now + timedelta(days=2)).isoformat()
    assert recency_weight(future, now=now) > 0


def _seed_paper(db: PaperDB, idx: int) -> str:
    ingested_at = datetime(2026, 2, 1, tzinfo=timezone.utc).isoformat()
    paper_id = f"paper-{idx}"
    paper = PaperRecord(
        id=paper_id,
        title=f"Paper {idx}",
        path=f"/tmp/paper-{idx}.md",
        ingested_at=ingested_at,
        mtime=float(idx),
        doi=None,
        arxiv_id=None,
        published_date="2025",
        summary=f"Summary {idx}",
    )
    db.upsert_paper(paper, f"Full text {idx}")
    return paper_id


def test_compute_mix_targets_uses_eight_two_split() -> None:
    assert _compute_mix_targets(10, recycled_available=5) == (8, 2)
    assert _compute_mix_targets(5, recycled_available=5) == (4, 1)
    assert _compute_mix_targets(4, recycled_available=5) == (4, 0)


def test_generate_daily_quiz_recycles_wrong_questions_at_target_ratio() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = PaperDB(Path(tmpdir) / "quiz.db")
        db.initialize()
        paper_ids = [_seed_paper(db, idx) for idx in range(10)]

        wrong_prompts = {
            "RECYCLE_Q_1": paper_ids[0],
            "RECYCLE_Q_2": paper_ids[1],
            "RECYCLE_Q_3": paper_ids[2],
        }
        for prompt, paper_id in wrong_prompts.items():
            qid = f"wrong-{prompt}"
            db.save_quiz_question(
                question_id=qid,
                paper_id=paper_id,
                question_text=prompt,
                expected_answer="expected",
            )
            db.update_quiz_answer(qid, user_answer="wrong", score=0.2)

        questions = generate_daily_quiz(db, count=10)
        recycled_count = sum(1 for q in questions if q.prompt in wrong_prompts)

        assert len(questions) == 10
        assert recycled_count == 2
        db.close()


def test_wrong_pool_ignores_questions_corrected_on_later_attempt() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = PaperDB(Path(tmpdir) / "quiz.db")
        db.initialize()
        paper_id = _seed_paper(db, 1)

        db.save_quiz_question(
            question_id="attempt-1",
            paper_id=paper_id,
            question_text="Recover me",
            expected_answer="x",
        )
        db.update_quiz_answer("attempt-1", user_answer="bad", score=0.1)

        db.save_quiz_question(
            question_id="attempt-2",
            paper_id=paper_id,
            question_text="Recover me",
            expected_answer="x",
        )
        db.update_quiz_answer("attempt-2", user_answer="good", score=1.0)

        pool = db.wrong_question_pool(limit=10)
        assert len(pool) == 0
        db.close()
