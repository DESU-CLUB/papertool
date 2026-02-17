from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile

from papertool.db import PaperDB
from papertool.graph import export_graph_html
from papertool.models import PaperRecord


def _seed_paper(db: PaperDB, root: Path, paper_id: str, title: str, text: str) -> None:
    path = root / f"{paper_id}.md"
    path.write_text(text, encoding="utf-8")
    rec = PaperRecord(
        id=paper_id,
        title=title,
        path=str(path),
        ingested_at=datetime(2026, 2, 13, tzinfo=timezone.utc).isoformat(),
        mtime=path.stat().st_mtime,
        summary=text[:120],
    )
    db.upsert_paper(rec, text)
    db.insert_chunks(paper_id, [text])


def test_export_graph_html_includes_zoom_and_fit_controls() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        db = PaperDB(root / "db.sqlite")
        db.initialize()
        _seed_paper(db, root, "p1", "Paper One", "Body one")
        _seed_paper(db, root, "p2", "Paper Two", "Body two")
        db.set_citations("p1", [("p2", "title:paper two", 0.82)])

        out = export_graph_html(db, root / "graph.html")
        html = out.read_text(encoding="utf-8")

        assert "const zoom = d3.zoom()" in html
        assert "function fitToScreen" in html
        assert "simulation.on('end'" in html
        assert "window.addEventListener('resize'" in html
        assert "svg.on('dblclick', () => fitToScreen(true));" in html
        db.close()
