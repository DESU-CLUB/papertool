from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile

from papertool.db import PaperDB
from papertool.ingest import rebuild_citation_graph
from papertool.models import PaperRecord


def _seed_paper(
    db: PaperDB,
    root: Path,
    paper_id: str,
    title: str,
    text: str,
    *,
    doi: str | None = None,
    arxiv_id: str | None = None,
    year: str | None = None,
) -> None:
    path = root / f"{paper_id}.md"
    path.write_text(text, encoding="utf-8")
    record = PaperRecord(
        id=paper_id,
        title=title,
        path=str(path.resolve()),
        ingested_at=datetime(2026, 2, 13, tzinfo=timezone.utc).isoformat(),
        mtime=path.stat().st_mtime,
        doi=doi,
        arxiv_id=arxiv_id,
        published_date=year,
        summary=text[:200],
    )
    db.upsert_paper(record, text)
    db.insert_chunks(paper_id, [text])


def test_rebuild_uses_local_only_pipeline() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        db = PaperDB(root / "db.sqlite")
        db.initialize()
        _seed_paper(
            db,
            root,
            "fa1",
            "FlashAttention: Fast and Memory-Efficient Exact Attention with IO-Awareness",
            "FlashAttention-1 body.",
            year="2022",
        )
        _seed_paper(
            db,
            root,
            "fa2",
            "FlashAttention-2:",
            """
            FlashAttention-2:
            Faster Attention with Better Parallelism and Work Partitioning

            References
            [5] Tri Dao, Daniel Y. Fu, Stefano Ermon, Atri Rudra, and Christopher Re.
            FlashAttention: Fast and memory-efficient exact attention with IO-awareness.
            In Advances in Neural Information Processing Systems, 2022.
            """,
            year="2023",
        )

        result = rebuild_citation_graph(db)
        assert result["ok"] is True
        edges = db.citation_edges_for_paper("fa2")
        assert any(row["target_paper_id"] == "fa1" and str(row["reason"]).startswith("title:") for row in edges["outgoing"])
        db.close()


def test_rebuild_local_only_uses_conservative_title_fallback() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        db = PaperDB(root / "db.sqlite")
        db.initialize()
        _seed_paper(
            db,
            root,
            "fa1",
            "FlashAttention: Fast and Memory-Efficient Exact Attention with IO-Awareness",
            "FlashAttention-1 body.",
            year="2022",
        )
        _seed_paper(
            db,
            root,
            "fa2",
            "FlashAttention-2: Faster Attention with Better Parallelism and Work Partitioning",
            """
            FlashAttention-2 paper body.

            References
            [5] Tri Dao, Daniel Y. Fu, Stefano Ermon, Atri Rudra, and Christopher Re.
            FlashAttention: Fast and memory-efficient exact attention with IO-awareness.
            In Advances in Neural Information Processing Systems, 2022.
            """,
            year="2023",
        )

        result = rebuild_citation_graph(db)
        assert result["ok"] is True
        edges = db.citation_edges_for_paper("fa2")
        assert any(row["target_paper_id"] == "fa1" and str(row["reason"]).startswith("title:") for row in edges["outgoing"])
        db.close()


def test_citation_status_summary_counts_reasons() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        db = PaperDB(root / "db.sqlite")
        db.initialize()
        _seed_paper(db, root, "a", "Paper A", "body", year="2022")
        _seed_paper(db, root, "b", "Paper B", "body", year="2023")
        db.set_citations("a", [("b", "title:paper b", 0.82)])
        summary = db.citation_status_summary()
        assert summary["total_edges"] == 1
        assert summary["reason_breakdown"]["title"] == 1
        inspect = db.citation_edges_for_paper("a")
        assert len(inspect["outgoing"]) == 1
        assert len(inspect["incoming"]) == 0
        db.close()


def test_rebuild_accepts_unique_paper_id_prefix() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        db = PaperDB(root / "db.sqlite")
        db.initialize()
        _seed_paper(
            db,
            root,
            "70248ec2bbbbbbbb",
            "YARN: Efficient context window extension of large language models",
            "YARN paper body.",
            arxiv_id="2309.00071",
            year="2023",
        )
        _seed_paper(
            db,
            root,
            "9047bb47aaaaaaaa",
            "DeepSeek-V2",
            """
            DeepSeek-V2 paper body.

            References
            B. Peng, J. Quesnelle, H. Fan, and E. Shippole.
            Yarn: Efficient context window extension of large language models.
            arXiv preprint arXiv:2309.00071, 2023.
            """,
            year="2024",
        )

        result = rebuild_citation_graph(db, paper_ids=["9047bb47"])
        assert result["ok"] is True
        assert result["processed"] == 1
        assert "9047bb47aaaaaaaa" in result["resolved_ids"]
        assert result["unresolved_ids"] == []
        edges = db.citation_edges_for_paper("9047bb47")
        assert any(
            row["target_paper_id"] == "70248ec2bbbbbbbb" and str(row["reason"]).startswith("arxiv:")
            for row in edges["outgoing"]
        )
        db.close()
