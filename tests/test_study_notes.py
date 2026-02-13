from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile

from papertool.config import PaperToolConfig
from papertool.db import PaperDB
from papertool.models import PaperRecord
from papertool.obsidian import (
    append_qa_to_paper_note,
    append_quiz_entry_to_paper_note,
    sync_review_prompts_in_paper_note,
    upsert_paper_note,
)


def _cfg(root: Path) -> PaperToolConfig:
    return PaperToolConfig(
        library_dir=root / "library",
        db_path=root / ".papertool" / "papertool.db",
        obsidian_vault=root / "vault",
    )


def test_db_log_qa_stores_compact_summary_not_full_transcript() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        db = PaperDB(root / "db.sqlite")
        db.initialize()
        answer = """
        Question: What is FlashAttention?
        Best matching evidence from your library:
        - Paper A ...
        Answer draft:
        FlashAttention is an IO-aware exact attention algorithm using tiling and online softmax.
        """
        db.log_qa("What is FlashAttention?", answer, paper_ids=["p1"], channel="cli")
        rows = db.recent_qa(limit=1)
        assert len(rows) == 1
        assert "Best matching evidence" not in str(rows[0]["answer"])
        assert "FlashAttention is an IO-aware exact attention algorithm" in str(rows[0]["answer"])
        db.close()


def test_study_note_writes_summary_and_hidden_quiz_answer() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = _cfg(root)
        note = upsert_paper_note(
            cfg,
            title="FlashAttention",
            source_path="/tmp/fa.pdf",
            summary="summary",
            doi=None,
            arxiv_id=None,
        )
        append_qa_to_paper_note(
            cfg,
            title="FlashAttention",
            question="What changed in FA2?",
            answer="Better parallel work partitioning across warps.",
            evidence_snippets=["[FlashAttention]-2 improves occupancy and throughput."],
        )
        append_quiz_entry_to_paper_note(
            cfg,
            title="FlashAttention",
            question="Explain online softmax update rule.",
            source="review",
            answered=False,
            score=None,
        )
        sync_review_prompts_in_paper_note(
            cfg,
            title="FlashAttention",
            prompts=["Explain online softmax update rule.", "Explain online softmax update rule."],
        )
        body = note.read_text(encoding="utf-8")
        assert "## Study Notes" in body
        assert "- Prompt: What changed in FA2?" in body
        assert "- Takeaway: Better parallel work partitioning across warps." in body
        assert "FlashAttention-2 improves occupancy and throughput." in body
        assert "- Expected Answer: [hidden for spaced repetition]" in body
        assert body.count("Explain online softmax update rule.") == 2  # one quiz entry, one review prompt line


def test_quiz_prompts_for_paper_returns_unique_latest_prompts() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        db = PaperDB(root / "db.sqlite")
        db.initialize()
        paper_path = root / "paper.md"
        paper_path.write_text("text", encoding="utf-8")
        paper = PaperRecord(
            id="p1",
            title="P1",
            path=str(paper_path),
            ingested_at=datetime(2026, 2, 13, tzinfo=timezone.utc).isoformat(),
            mtime=paper_path.stat().st_mtime,
            summary="summary",
        )
        db.upsert_paper(paper, "text")
        db.insert_chunks("p1", ["text"])
        db.save_quiz_question(
            question_id="q1",
            paper_id="p1",
            question_text="What is the key idea?",
            expected_answer="A",
            source="daily",
        )
        db.save_quiz_question(
            question_id="q2",
            paper_id="p1",
            question_text="What is the key idea?",
            expected_answer="B",
            source="review",
        )
        db.save_quiz_question(
            question_id="q3",
            paper_id="p1",
            question_text="How is softmax stabilized?",
            expected_answer="C",
            source="review",
        )
        prompts = db.quiz_prompts_for_paper("p1", limit=10)
        texts = [str(row["question_text"]) for row in prompts]
        assert len(texts) == 2
        assert "What is the key idea?" in texts
        assert "How is softmax stabilized?" in texts
        db.close()
