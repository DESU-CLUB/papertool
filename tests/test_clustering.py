from datetime import datetime, timezone
from pathlib import Path
import tempfile

from papertool.db import PaperDB
from papertool.models import PaperRecord
from papertool.rust_backend import build_clusters


def _seed(db: PaperDB, root: Path, paper_id: str, title: str, text: str) -> None:
    path = root / f"{paper_id}.md"
    path.write_text(text, encoding="utf-8")
    record = PaperRecord(
        id=paper_id,
        title=title,
        path=str(path),
        ingested_at=datetime(2026, 2, 11, tzinfo=timezone.utc).isoformat(),
        mtime=path.stat().st_mtime,
        summary=text[:200],
    )
    db.upsert_paper(record, text)
    db.insert_chunks(paper_id, [text])


def test_cluster_build_persists_topic_and_community_views() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        db = PaperDB(root / "c.db")
        db.initialize()

        _seed(db, root, "p1", "MoE Routing", "moe router sparse experts gating")
        _seed(db, root, "p2", "Mamba State Space", "mamba selective state space model")
        _seed(db, root, "p3", "Attention", "attention kv cache flash attention")

        db.set_citations("p1", [("p2", "ref", 0.9)])
        db.set_citations("p2", [("p3", "ref", 0.9)])

        result = build_clusters(db, root / "index")
        assert result["ok"] is True

        topic_rows = db.cluster_overview("topic", limit=100)
        assert len(topic_rows) > 0

        comm_rows = db.cluster_overview("community", limit=100)
        assert len(comm_rows) > 0

        first_comm = str(comm_rows[0]["cluster_key"])
        members = db.cluster_papers(community_id=first_comm, limit=100)
        assert len(members) > 0
        db.close()
