from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile

from papertool.config import PaperToolConfig
from papertool.medals import link_repo_to_paper
from papertool.models import PaperRecord
from papertool.store.couch_store import CouchStore


def _cfg(root: Path, db_name: str) -> PaperToolConfig:
    return PaperToolConfig(
        library_dir=root / "library",
        db_path=root / db_name,
        storage_backend="hybrid",
    )


def _seed(store: CouchStore, root: Path, paper_id: str) -> None:
    path = root / f"{paper_id}.md"
    path.write_text("flash attention notes", encoding="utf-8")
    record = PaperRecord(
        id=paper_id,
        title="FlashAttention",
        path=str(path),
        ingested_at=datetime(2026, 2, 13, tzinfo=timezone.utc).isoformat(),
        mtime=path.stat().st_mtime,
        summary="io-aware attention",
    )
    store.db.upsert_paper(record, "full text")
    store.db.insert_chunks(paper_id, ["io aware algorithm"])


def test_medal_tables_roundtrip_via_couch_doc_mapping() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        source = CouchStore(_cfg(root, "source.db"))
        target = CouchStore(_cfg(root, "target.db"))
        source.initialize()
        target.initialize()
        try:
            source.db.set_goal_settings(1, "UTC")
            _seed(source, root, "p1")
            source.db.queue_set_status("p1", "done")
            source.db.save_quiz_question(
                question_id="daily-q1",
                paper_id="p1",
                question_text="What changed in FlashAttention?",
                expected_answer="Expected",
                source="daily",
            )
            source.db.update_quiz_answer("daily-q1", user_answer="answer", score=8)
            link_repo_to_paper(source.db, "p1", "https://github.com/DESU-CLUB/flash-attention")

            docs = source._docs_from_local()
            stats = target._apply_remote_docs(docs)

            medal = target.db.get_paper_medal("p1")
            progress = target.db.daily_progress_rows(limit=10)
            links = target.db.paper_repo_links("p1")

            assert int(stats.get("paper_medals", 0)) >= 1
            assert int(stats.get("daily_progress", 0)) >= 1
            assert int(stats.get("paper_repo_links", 0)) >= 1
            assert medal is not None
            assert medal["bronze_awarded_at"] is not None
            assert medal["gold_awarded_at"] is not None
            assert len(progress) >= 1
            assert len(links) == 1
            assert links[0]["is_owner_valid"] is True
        finally:
            source.close()
            target.close()
