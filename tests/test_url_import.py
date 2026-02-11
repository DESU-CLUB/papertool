from datetime import datetime, timezone
from pathlib import Path
import tempfile

from papertool.db import PaperDB
from papertool.models import PaperRecord
from papertool.url_import import (
    arxiv_abs_to_pdf,
    canonicalize_arxiv_id,
    detect_resource_kind,
    extract_arxiv_id_from_url,
    import_url_to_library,
    normalize_input_url,
)


def test_detect_resource_kind_for_known_sources() -> None:
    assert detect_resource_kind("https://arxiv.org/abs/2205.14135") == "arxiv"
    assert detect_resource_kind("https://arxiv.org/pdf/2205.14135.pdf") == "arxiv"
    assert detect_resource_kind("https://example.com/paper.pdf") == "pdf"
    assert detect_resource_kind("https://github.com/owner/repo") == "github"
    assert detect_resource_kind("https://x.com/user/status/123") == "x_post"
    assert detect_resource_kind("https://example.com/post") == "webpage"


def test_arxiv_abs_to_pdf_conversion() -> None:
    assert arxiv_abs_to_pdf("https://arxiv.org/abs/2205.14135") == "https://arxiv.org/pdf/2205.14135.pdf"


def test_normalize_input_url_adds_https() -> None:
    assert normalize_input_url("example.com") == "https://example.com"


def test_extract_arxiv_id_from_arxiv_urls() -> None:
    assert extract_arxiv_id_from_url("https://arxiv.org/abs/2205.14135") == "2205.14135"
    assert extract_arxiv_id_from_url("https://arxiv.org/pdf/2205.14135.pdf") == "2205.14135"


def test_canonicalize_arxiv_id_drops_version() -> None:
    assert canonicalize_arxiv_id("2405.04434v5") == "2405.04434"
    assert canonicalize_arxiv_id("arXiv:2205.14135v2") == "2205.14135"


def test_import_arxiv_dedupes_when_existing_id_present() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        db = PaperDB(root / "db.sqlite")
        db.initialize()

        existing_path = root / "library" / "captures" / "papers" / "2205.14135.pdf"
        existing_path.parent.mkdir(parents=True, exist_ok=True)
        existing_path.write_text("dummy", encoding="utf-8")

        paper = PaperRecord(
            id="p1",
            title="Existing Paper",
            path=str(existing_path.resolve()),
            ingested_at=datetime(2026, 2, 11, tzinfo=timezone.utc).isoformat(),
            mtime=existing_path.stat().st_mtime,
            arxiv_id="2205.14135",
        )
        db.upsert_paper(paper, "existing text")

        result = import_url_to_library(db, root / "library", "https://arxiv.org/abs/2205.14135")
        assert result.paper_id == "p1"
        assert result.saved_path == str(existing_path.resolve())
        assert result.title == "2205.14135"
        db.close()


def test_import_arxiv_dedupes_against_existing_versioned_id() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        db = PaperDB(root / "db.sqlite")
        db.initialize()

        existing_path = root / "library" / "captures" / "papers" / "2405.04434.pdf"
        existing_path.parent.mkdir(parents=True, exist_ok=True)
        existing_path.write_text("dummy", encoding="utf-8")

        paper = PaperRecord(
            id="p2",
            title="Existing Versioned Paper",
            path=str(existing_path.resolve()),
            ingested_at=datetime(2026, 2, 11, tzinfo=timezone.utc).isoformat(),
            mtime=existing_path.stat().st_mtime,
            arxiv_id="2405.04434v5",
        )
        db.upsert_paper(paper, "existing text")

        result = import_url_to_library(db, root / "library", "https://arxiv.org/abs/2405.04434")
        assert result.paper_id == "p2"
        assert result.title == "2405.04434"
        db.close()
