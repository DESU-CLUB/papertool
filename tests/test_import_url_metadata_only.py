from __future__ import annotations

from pathlib import Path
import tempfile

from papertool.db import PaperDB
from papertool.url_import import import_url_to_library


def test_x_and_blog_import_do_not_fetch_remote_content(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        db = PaperDB(root / "import.db")
        db.initialize()

        def _fail_fetch(*_args, **_kwargs):
            raise AssertionError("_http_get should not be called for metadata-only resources")

        monkeypatch.setattr("papertool.url_import._http_get", _fail_fetch)

        x_result = import_url_to_library(
            db,
            root / "library",
            "https://x.com/user/status/999",
            page_title="X Thread",
        )
        blog_result = import_url_to_library(
            db,
            root / "library",
            "https://example.com/blog/intro",
            page_title="Blog Post",
            kind_override="blog",
        )

        assert x_result.entity_type == "resource"
        assert blog_result.entity_type == "resource"
        assert db.get_resource(str(x_result.entity_id)) is not None
        assert db.get_resource(str(blog_result.entity_id)) is not None
        db.close()


def test_arxiv_pdf_import_still_uses_fetch_path(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        db = PaperDB(root / "import.db")
        db.initialize()
        calls = {"fetch": 0}

        def _fake_fetch(*_args, **_kwargs):
            calls["fetch"] += 1
            return (b"%PDF-1.4\n%%EOF\n", "application/pdf")

        monkeypatch.setattr("papertool.url_import._http_get", _fake_fetch)
        monkeypatch.setattr("papertool.url_import._ingest_saved_file", lambda *_args, **_kwargs: "paper-stub")

        result = import_url_to_library(
            db,
            root / "library",
            "https://arxiv.org/abs/2205.14135",
        )

        assert calls["fetch"] >= 1
        assert result.entity_type == "paper"
        assert result.paper_id == "paper-stub"
        db.close()
