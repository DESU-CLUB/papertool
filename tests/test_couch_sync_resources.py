from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile

from papertool.config import PaperToolConfig
from papertool.models import PaperRecord
from papertool.resources import link_resource_to_paper
from papertool.store.couch_store import CouchStore
from papertool.url_import import import_url_to_library


def _cfg(root: Path, db_name: str) -> PaperToolConfig:
    return PaperToolConfig(
        library_dir=root / "library",
        db_path=root / db_name,
        storage_backend="hybrid",
    )


def _seed_paper(store: CouchStore, root: Path, paper_id: str) -> None:
    path = root / f"{paper_id}.md"
    path.write_text("attention overview", encoding="utf-8")
    record = PaperRecord(
        id=paper_id,
        title="Attention Paper",
        path=str(path),
        ingested_at=datetime(2026, 2, 13, tzinfo=timezone.utc).isoformat(),
        mtime=path.stat().st_mtime,
        summary="attention",
    )
    store.db.upsert_paper(record, "attention")
    store.db.insert_chunks(paper_id, ["attention"])
    topic_id = store.db.topic_id_for_label("attention")
    if topic_id:
        store.db.replace_paper_topics(paper_id, [(topic_id, 1.0)])


def test_resource_tables_roundtrip_via_couch_doc_mapping() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        source = CouchStore(_cfg(root, "source.db"))
        target = CouchStore(_cfg(root, "target.db"))
        source.initialize()
        target.initialize()
        try:
            _seed_paper(source, root, "p1")
            result = import_url_to_library(
                source.db,
                root / "library",
                "https://example.com/blog/attention-updates",
                page_title="Attention Updates",
                topics=["attention"],
                kind_override="blog",
            )
            assert result.entity_id is not None
            link_resource_to_paper(
                source.db,
                resource_id=str(result.entity_id),
                paper_id="p1",
                link_type="related",
            )

            docs = source._docs_from_local()
            stats = target._apply_remote_docs(docs)
            resources = target.db.list_resources(limit=10)
            links = target.db.resource_links_for_paper("p1", limit=10)
            tags = target.db.resource_topics(str(result.entity_id))

            assert int(stats.get("resources", 0)) >= 1
            assert int(stats.get("resource_topics", 0)) >= 1
            assert int(stats.get("paper_resource_links", 0)) >= 1
            assert len(resources) == 1
            assert len(links) == 1
            assert len(tags) >= 1
        finally:
            source.close()
            target.close()
