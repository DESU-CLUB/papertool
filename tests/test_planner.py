from datetime import datetime, timezone
from pathlib import Path
import tempfile

from papertool.db import PaperDB
from papertool.models import PaperRecord
from papertool.planner import due_review_questions, generate_micro_quiz_for_paper, paper_of_day_payload


def _seed_paper(db: PaperDB, idx: int, ingested_at: str) -> str:
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


def test_plan_today_moves_items_to_today_queue() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = PaperDB(Path(tmpdir) / "queue.db")
        db.initialize()
        now = datetime(2026, 2, 11, tzinfo=timezone.utc).isoformat()
        p1 = _seed_paper(db, 1, now)
        p2 = _seed_paper(db, 2, now)

        db.queue_set_status(p1, "next", priority=2.0)
        db.queue_set_status(p2, "inbox", priority=1.0)

        today_items = db.plan_today(max_items=2)
        assert len(today_items) == 2
        statuses = {row["status"] for row in db.queue_list(status="today", limit=10)}
        assert statuses == {"today"}
        db.close()


def test_paper_of_day_and_micro_quiz() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = PaperDB(Path(tmpdir) / "pod.db")
        db.initialize()
        now = datetime(2026, 2, 11, tzinfo=timezone.utc).isoformat()
        paper_id = _seed_paper(db, 1, now)
        db.queue_set_status(paper_id, "today", priority=3.0)

        payload = paper_of_day_payload(db)
        assert payload is not None
        assert payload["paper_id"] == paper_id

        quiz = generate_micro_quiz_for_paper(db, paper_id, count=3)
        assert len(quiz) == 3
        db.close()


def test_submit_answer_schedules_spaced_review_and_due_generation() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = PaperDB(Path(tmpdir) / "review.db")
        db.initialize()
        now = datetime(2026, 2, 11, tzinfo=timezone.utc).isoformat()
        paper_id = _seed_paper(db, 1, now)

        db.save_quiz_question(
            question_id="q-1",
            paper_id=paper_id,
            question_text="What is the key idea?",
            expected_answer="Expected",
            source="micro",
        )
        db.update_quiz_answer("q-1", "My answer", 0.2)

        cards = db.conn.execute("SELECT * FROM review_cards WHERE paper_id = ?", (paper_id,)).fetchall()
        assert len(cards) == 1

        # Force due for deterministic test.
        db.conn.execute("UPDATE review_cards SET next_due_at = '2000-01-01T00:00:00+00:00'")
        db.conn.commit()

        due_questions = due_review_questions(db, count=5)
        assert len(due_questions) == 1
        assert due_questions[0].paper_id == paper_id
        db.close()
