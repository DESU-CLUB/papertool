from datetime import datetime, timezone
from pathlib import Path
import tempfile

from papertool.config import PaperToolConfig
from papertool.db import PaperDB
from papertool.models import PaperRecord
from papertool.retrieval import retrieve


def _seed_paper(db: PaperDB, root: Path, paper_id: str, title: str, text: str, status: str) -> None:
    path = root / f"{paper_id}.md"
    path.write_text(text, encoding="utf-8")
    paper = PaperRecord(
        id=paper_id,
        title=title,
        path=str(path),
        ingested_at=datetime(2026, 2, 11, tzinfo=timezone.utc).isoformat(),
        mtime=path.stat().st_mtime,
        summary=text[:120],
    )
    db.upsert_paper(paper, text)
    db.insert_chunks(paper_id, [text])
    db.queue_set_status(paper_id, status, priority=1.0)


def test_python_hybrid_retrieval_applies_queue_boost() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        db = PaperDB(root / "r.db")
        db.initialize()

        text = "flash attention exact io aware memory efficient"
        _seed_paper(db, root, "paper-done", "Done Paper", text, "done")
        _seed_paper(db, root, "paper-today", "Today Paper", text, "today")

        cfg = PaperToolConfig(
            library_dir=root / "library",
            db_path=root / "r.db",
            retrieval_backend="python",
            rust_index_dir=root / "index",
            cluster_mode="on_demand",
        )
        hits = retrieve(db, "flash attention", top_k=2, config=cfg)
        assert len(hits) == 2
        assert hits[0].paper_id == "paper-today"
        db.close()


def test_topic_filter_narrows_retrieval_scope() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        db = PaperDB(root / "r.db")
        db.initialize()

        _seed_paper(db, root, "paper-attn", "Attention", "attention heads kv cache", "next")
        _seed_paper(db, root, "paper-moe", "MoE", "mixture of experts router", "next")

        topic_id = db.upsert_topic("attention", source="seed")
        db.replace_paper_topics("paper-attn", [(topic_id, 0.95)])
        db.replace_paper_topics("paper-moe", [])

        cfg = PaperToolConfig(
            library_dir=root / "library",
            db_path=root / "r.db",
            retrieval_backend="python",
            rust_index_dir=root / "index",
            cluster_mode="on_demand",
        )
        hits = retrieve(db, "attention", top_k=5, topic="attention", config=cfg)
        assert len(hits) >= 1
        assert all(hit.paper_id == "paper-attn" for hit in hits)
        db.close()
