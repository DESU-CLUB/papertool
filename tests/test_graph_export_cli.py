from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
import tempfile

from typer.testing import CliRunner

from papertool.cli import app
from papertool.config import PaperToolConfig, dump_config
from papertool.db import PaperDB
from papertool.models import PaperRecord


def _seed_paper(
    db: PaperDB,
    root: Path,
    paper_id: str,
    title: str,
    text: str,
    *,
    arxiv_id: str | None = None,
) -> None:
    path = root / f"{paper_id}.md"
    path.write_text(text, encoding="utf-8")
    rec = PaperRecord(
        id=paper_id,
        title=title,
        path=str(path),
        ingested_at=datetime(2026, 2, 13, tzinfo=timezone.utc).isoformat(),
        mtime=path.stat().st_mtime,
        summary=text[:120],
        arxiv_id=arxiv_id,
    )
    db.upsert_paper(rec, text)
    db.insert_chunks(paper_id, [text])


def _cfg(root: Path) -> PaperToolConfig:
    return PaperToolConfig(
        library_dir=root / "library",
        db_path=root / "papertool.db",
        retrieval_backend="python",
        rust_index_dir=root / "index",
        cluster_mode="on_demand",
        storage_backend="sqlite",
    )


def test_graph_export_rebuilds_citations_before_writing_graph() -> None:
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = _cfg(root)
        dump_config(cfg, root / "papertool.toml")

        db = PaperDB(cfg.db_path)
        db.initialize()
        _seed_paper(
            db,
            root,
            "target",
            "YARN: Efficient context window extension",
            "Target paper body",
            arxiv_id="2309.00071",
        )
        _seed_paper(
            db,
            root,
            "source",
            "DeepSeek-V2",
            "References\nB. Peng et al. YARN. arXiv preprint arXiv:2309.00071, 2023.",
        )
        # Start stale to prove export path rebuilds and repopulates edges.
        db.conn.execute("DELETE FROM citations")
        db.conn.commit()
        db.close()

        output_path = root / "graph.json"
        prev = Path.cwd()
        try:
            os.chdir(root)
            result = runner.invoke(
                app,
                ["graph", "export", "--format", "json", "--output", str(output_path)],
                catch_exceptions=False,
            )
        finally:
            os.chdir(prev)

        assert result.exit_code == 0
        assert output_path.exists()
        graph_payload = json.loads(output_path.read_text(encoding="utf-8"))
        links = graph_payload.get("links", [])
        assert isinstance(links, list)
        assert any(str(link.get("source")) == "source" and str(link.get("target")) == "target" for link in links)
        assert "Citation rebuild: processed=" in result.stdout


def test_graph_export_fails_when_rebuild_fails(monkeypatch) -> None:
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = _cfg(root)
        dump_config(cfg, root / "papertool.toml")

        db = PaperDB(cfg.db_path)
        db.initialize()
        db.close()

        def _fail_rebuild(_db: PaperDB, paper_ids=None, config=None):  # type: ignore[no-untyped-def]
            return {
                "ok": False,
                "processed": 0,
                "edges_set": 0,
                "error": "forced_failure",
            }

        monkeypatch.setattr("papertool.cli.rebuild_citation_graph", _fail_rebuild)

        output_path = root / "graph.json"
        prev = Path.cwd()
        try:
            os.chdir(root)
            result = runner.invoke(
                app,
                ["graph", "export", "--format", "json", "--output", str(output_path)],
                catch_exceptions=False,
            )
        finally:
            os.chdir(prev)

        assert result.exit_code == 1
        assert "Citation rebuild failed" in result.stderr
        assert not output_path.exists()
