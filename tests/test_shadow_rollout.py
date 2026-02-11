from datetime import datetime, timezone
from pathlib import Path
import tempfile

from papertool.config import PaperToolConfig
from papertool.db import PaperDB
from papertool.models import PaperRecord
from papertool.retrieval import retrieve


def _seed(db: PaperDB, root: Path, paper_id: str, text: str) -> None:
    path = root / f"{paper_id}.md"
    path.write_text(text, encoding="utf-8")
    rec = PaperRecord(
        id=paper_id,
        title=f"Title {paper_id}",
        path=str(path),
        ingested_at=datetime(2026, 2, 11, tzinfo=timezone.utc).isoformat(),
        mtime=path.stat().st_mtime,
        summary=text,
    )
    db.upsert_paper(rec, text)
    db.insert_chunks(paper_id, [text])


def test_shadow_mode_logs_python_vs_rust_comparison() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        db = PaperDB(root / "s.db")
        db.initialize()

        _seed(db, root, "p1", "flash attention io awareness")
        _seed(db, root, "p2", "mamba state space")

        cfg = PaperToolConfig(
            library_dir=root / "library",
            db_path=root / "s.db",
            retrieval_backend="shadow",
            rust_index_dir=root / "index",
            cluster_mode="on_demand",
        )

        hits = retrieve(db, "flash attention", top_k=5, config=cfg)
        assert len(hits) >= 1

        logs = db.recent_shadow_logs(limit=5)
        assert len(logs) == 1
        assert logs[0]["query"] == "flash attention"
        assert 0.0 <= float(logs[0]["overlap_at_k"]) <= 1.0
        db.close()


def test_rust_backend_flag_falls_back_to_python_when_unavailable() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        db = PaperDB(root / "s.db")
        db.initialize()
        _seed(db, root, "p1", "retrieval augmented generation")

        cfg = PaperToolConfig(
            library_dir=root / "library",
            db_path=root / "s.db",
            retrieval_backend="rust",
            rust_index_dir=root / "index",
            cluster_mode="on_demand",
        )

        hits = retrieve(db, "retrieval", top_k=3, config=cfg)
        assert len(hits) == 1
        assert hits[0].paper_id == "p1"
        db.close()
