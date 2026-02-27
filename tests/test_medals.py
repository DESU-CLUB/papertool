from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile

import pytest

from papertool.dashboard import build_medals_dashboard
from papertool.db import PaperDB, normalize_score_input
from papertool.medals import link_repo_to_paper, parse_github_repo_link
from papertool.models import PaperRecord


def _seed_paper(db: PaperDB, root: Path, idx: int) -> str:
    path = root / f"paper-{idx}.md"
    path.write_text(f"paper {idx}", encoding="utf-8")
    paper_id = f"paper-{idx}"
    record = PaperRecord(
        id=paper_id,
        title=f"Paper {idx}",
        path=str(path),
        ingested_at=datetime(2026, 2, 13, tzinfo=timezone.utc).isoformat(),
        mtime=path.stat().st_mtime,
        summary=f"Summary {idx}",
    )
    db.upsert_paper(record, f"Full text {idx}")
    db.insert_chunks(paper_id, [f"chunk {idx}"])
    return paper_id


def test_score_normalization_accepts_zero_to_one_and_zero_to_ten() -> None:
    assert normalize_score_input(0.8) == 0.8
    assert normalize_score_input(8) == 0.8
    assert normalize_score_input(9.5) == 0.95


def test_score_normalization_rejects_invalid_range() -> None:
    with pytest.raises(ValueError):
        normalize_score_input(-0.1)
    with pytest.raises(ValueError):
        normalize_score_input(11)


def test_github_parser_and_owner_validation() -> None:
    valid = parse_github_repo_link("https://github.com/DESU-CLUB/papertool")
    assert valid.owner == "DESU-CLUB"
    assert valid.repo == "papertool"
    assert valid.is_owner_valid is True

    other = parse_github_repo_link("https://github.com/other/repo")
    assert other.is_owner_valid is False

    with pytest.raises(ValueError):
        parse_github_repo_link("ftp://github.com/DESU-CLUB/papertool")


def test_medal_transitions_bronze_silver_revoke_reactivate() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        db = PaperDB(root / "medals.db")
        db.initialize()
        db.set_goal_settings(1, "UTC")
        paper_id = _seed_paper(db, root, 1)

        db.queue_set_status(paper_id, "done")
        db.save_quiz_question(
            question_id="daily-q1",
            paper_id=paper_id,
            question_text="Daily check",
            expected_answer="Expected",
            source="daily",
        )
        db.update_quiz_answer("daily-q1", user_answer="ok", score=8)

        medal = db.get_paper_medal(paper_id)
        assert medal is not None
        assert medal["bronze_awarded_at"] is not None
        assert medal["silver_active"] is False

        db.save_quiz_question(
            question_id="review-q1",
            paper_id=paper_id,
            question_text="Review check",
            expected_answer="Expected",
            source="review",
        )
        db.update_quiz_answer("review-q1", user_answer="ok", score=9)
        medal = db.get_paper_medal(paper_id)
        assert medal is not None
        assert medal["silver_active"] is True
        first_silver_award = medal["silver_awarded_at"]
        assert first_silver_award is not None

        db.save_quiz_question(
            question_id="review-q2",
            paper_id=paper_id,
            question_text="Review check",
            expected_answer="Expected",
            source="review",
        )
        db.update_quiz_answer("review-q2", user_answer="bad", score=0.2)
        medal = db.get_paper_medal(paper_id)
        assert medal is not None
        assert medal["silver_active"] is False
        assert medal["silver_revoked_at"] is not None

        db.save_quiz_question(
            question_id="review-q3",
            paper_id=paper_id,
            question_text="Review check",
            expected_answer="Expected",
            source="review",
        )
        db.update_quiz_answer("review-q3", user_answer="great", score=10)
        medal = db.get_paper_medal(paper_id)
        assert medal is not None
        assert medal["silver_active"] is True
        assert medal["silver_awarded_at"] == first_silver_award

        event_rows = db.conn.execute(
            "SELECT event_type FROM medal_events WHERE paper_id = ?",
            (paper_id,),
        ).fetchall()
        event_types = [str(row["event_type"]) for row in event_rows]
        assert event_types.count("bronze_awarded") == 1
        assert "silver_awarded" in event_types
        assert "silver_revoked" in event_types
        assert "silver_reactivated" in event_types
        db.close()


def test_gold_requires_bronze_and_desu_club_repo() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        db = PaperDB(root / "gold.db")
        db.initialize()
        db.set_goal_settings(1, "UTC")
        paper_id = _seed_paper(db, root, 2)

        link_repo_to_paper(db, paper_id, "https://github.com/DESU-CLUB/flash-attention-notes")
        medal = db.get_paper_medal(paper_id)
        assert medal is not None
        assert medal["gold_awarded_at"] is None

        db.queue_set_status(paper_id, "done")
        db.save_quiz_question(
            question_id="daily-q2",
            paper_id=paper_id,
            question_text="Daily check",
            expected_answer="Expected",
            source="daily",
        )
        db.update_quiz_answer("daily-q2", user_answer="ok", score=1)

        medal = db.get_paper_medal(paper_id)
        assert medal is not None
        assert medal["bronze_awarded_at"] is not None
        assert medal["gold_awarded_at"] is not None
        assert medal["gold_repo_url"] == "https://github.com/DESU-CLUB/flash-attention-notes"
        db.close()


def test_dashboard_generation_writes_html_with_medal_summary() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        db = PaperDB(root / "dash.db")
        db.initialize()
        db.set_goal_settings(1, "UTC")
        paper_id = _seed_paper(db, root, 3)
        db.queue_set_status(paper_id, "done")
        db.save_quiz_question(
            question_id="daily-q3",
            paper_id=paper_id,
            question_text="Daily check",
            expected_answer="Expected",
            source="daily",
        )
        db.update_quiz_answer("daily-q3", user_answer="ok", score=8)

        output = root / "medals.html"
        built = build_medals_dashboard(db, output)
        body = built.read_text(encoding="utf-8")
        assert built == output
        assert "PaperTool Streaks & Medals" in body
        assert "Bronze / Silver / Gold" in body
        assert "Paper 3" in body
        assert "--medal-bronze: #b45309;" in body
        assert "--medal-silver: #9ca3af;" in body
        assert "--medal-gold: #ca8a04;" in body
        assert "--medal-muted: #94a3b8;" in body
        assert '.medal-bronze { background: var(--medal-bronze); color: #ffffff; }' in body
        assert '.medal-silver { background: var(--medal-silver); color: #111827; }' in body
        assert '.medal-gold { background: var(--medal-gold); color: #111827; }' in body
        assert '.medal-muted { background: var(--medal-muted); color: #0f172a; }' in body
        assert 'class="badge medal-bronze">Bronze</span>' in body
        assert 'class="badge medal-muted">Silver</span>' in body
        assert 'class="badge medal-muted">Gold</span>' in body
        db.close()
