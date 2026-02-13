from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile

from papertool.db import PaperDB
from papertool.models import PaperRecord
from papertool.resources import related_resources_for_paper
from papertool.url_import import import_url_to_library


def _seed_paper(db: PaperDB, root: Path, paper_id: str, title: str) -> str:
    path = root / f"{paper_id}.md"
    path.write_text("attention mechanisms and flash kernels", encoding="utf-8")
    record = PaperRecord(
        id=paper_id,
        title=title,
        path=str(path),
        ingested_at=datetime(2026, 2, 13, tzinfo=timezone.utc).isoformat(),
        mtime=path.stat().st_mtime,
        summary="attention kernels",
    )
    db.upsert_paper(record, "attention kernels")
    db.insert_chunks(paper_id, ["attention kernels"])
    return paper_id


def test_import_x_resource_metadata_only_and_tagging() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        db = PaperDB(root / "resources.db")
        db.initialize()

        result = import_url_to_library(
            db,
            root / "library",
            "https://x.com/user/status/12345",
            page_title="Flash Attention Thread",
            context_text="implementation details",
            topics=["attention", "systems"],
        )

        assert result.entity_type == "resource"
        assert result.paper_id is None
        assert result.entity_id is not None
        assert result.saved_path == ""

        resource = db.get_resource(str(result.entity_id))
        assert resource is not None
        assert resource["kind"] == "x_post"
        tags = db.resource_topics(str(result.entity_id))
        labels = {str(row["topic_label"]) for row in tags}
        assert "attention" in labels
        assert "systems" in labels
        db.close()


def test_resource_dedupes_by_canonical_url() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        db = PaperDB(root / "resources.db")
        db.initialize()

        first = import_url_to_library(
            db,
            root / "library",
            "https://example.com/blog/post?utm_source=test",
            page_title="Delta Notes",
            kind_override="blog",
        )
        second = import_url_to_library(
            db,
            root / "library",
            "https://example.com/blog/post/",
            page_title="Delta Notes Updated",
            kind_override="blog",
        )

        assert first.entity_type == "resource"
        assert first.entity_id == second.entity_id
        rows = db.list_resources(limit=10)
        assert len(rows) == 1
        db.close()


def test_related_resources_direct_and_topic_overlap() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        db = PaperDB(root / "resources.db")
        db.initialize()

        paper_id = _seed_paper(db, root, "p1", "FlashAttention")
        attention_topic = db.topic_id_for_label("attention")
        assert attention_topic is not None
        db.replace_paper_topics(paper_id, [(attention_topic, 1.0)])

        direct = import_url_to_library(
            db,
            root / "library",
            "https://x.com/user/status/111",
            page_title="Direct Resource",
            topics=["attention"],
            link_paper_id=paper_id,
        )
        overlap = import_url_to_library(
            db,
            root / "library",
            "https://example.com/blog/attention-deep-dive",
            page_title="Topic Overlap",
            topics=["attention"],
            kind_override="blog",
        )

        rows = related_resources_for_paper(db, paper_id, limit=10)
        resource_ids = {str(row["resource_id"]) for row in rows}
        assert str(direct.entity_id) in resource_ids
        assert str(overlap.entity_id) in resource_ids
        db.close()
