from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile

from typer.testing import CliRunner

from papertool.ask_service import commit_or_confirm, confirm_ask_session, get_scope_lock_status, prepare_ask_session, prepare_ask_with_lock
from papertool.cli import app
from papertool.config import PaperToolConfig, dump_config
from papertool.db import PaperDB
from papertool.models import PaperRecord
from papertool.query_scope import resolve_question_scope


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


def _cfg(root: Path) -> PaperToolConfig:
    return PaperToolConfig(
        library_dir=root / "library",
        db_path=root / "papertool.db",
        retrieval_backend="python",
        rust_index_dir=root / "index",
        cluster_mode="on_demand",
        storage_backend="sqlite",
    )


def test_prepare_ask_session_scopes_out_tangential_papers() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = _cfg(root)
        db = PaperDB(cfg.db_path)
        db.initialize()

        _seed_paper(
            db,
            root,
            "fa1",
            "FlashAttention: Fast and Memory-Efficient Exact Attention",
            "FlashAttention is an IO-aware exact attention algorithm.",
        )
        _seed_paper(
            db,
            root,
            "fa2",
            "FlashAttention-2: Faster Attention with Better Parallelism",
            "FlashAttention-2 improves parallel work partitioning.",
        )
        _seed_paper(
            db,
            root,
            "sora",
            "Open-Sora: Democratizing Efficient Video Production for All",
            "Open-Sora references FlashAttention in related work only.",
        )

        prepared = prepare_ask_session(
            db,
            cfg,
            question="What is FlashAttention?",
            top_k=10,
            channel="cli",
        )
        assert prepared["ok"] is True
        selected_ids = set(prepared["paper_ids"])
        assert "fa1" in selected_ids
        assert "fa2" in selected_ids
        assert "sora" not in selected_ids

        for row in prepared["sources"]:
            assert row["paper_id"] in selected_ids
        db.close()


def test_find_papers_by_id_prefix_behaviors() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = _cfg(root)
        db = PaperDB(cfg.db_path)
        db.initialize()
        _seed_paper(db, root, "9047bb47aaaaaaaa", "Paper A", "content a")
        _seed_paper(db, root, "9047bb88bbbbbbbb", "Paper B", "content b")
        _seed_paper(db, root, "9047cc99cccccccc", "Paper C", "content c")

        unique = db.find_papers_by_id_prefix("9047bb47")
        assert len(unique) == 1
        assert str(unique[0]["id"]) == "9047bb47aaaaaaaa"

        ambiguous = db.find_papers_by_id_prefix("9047")
        assert len(ambiguous) >= 2

        assert db.find_papers_by_id_prefix("904") == []
        assert db.find_papers_by_id_prefix("zzzz") == []
        db.close()


def test_prepare_ask_session_accepts_unique_paper_id_prefix() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = _cfg(root)
        db = PaperDB(cfg.db_path)
        db.initialize()
        _seed_paper(
            db,
            root,
            "9047bb47feedface",
            "MLA Paper",
            "Multi-head Latent Attention uses down-projection and up-projection.",
        )

        prepared = prepare_ask_session(
            db,
            cfg,
            question="Explain MLA projection",
            explicit_paper_ids=["9047bb47"],
            channel="cli",
        )
        assert prepared["ok"] is True
        assert prepared["paper_ids"] == ["9047bb47feedface"]
        selected = prepared["selected_papers"]
        assert isinstance(selected, list)
        assert selected
        assert selected[0]["reason"] == "explicit_paper_id_prefix"
        db.close()


def test_ambiguous_paper_id_prefix_returns_candidates() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = _cfg(root)
        db = PaperDB(cfg.db_path)
        db.initialize()
        _seed_paper(db, root, "9047bb47feedface", "Paper A", "alpha")
        _seed_paper(db, root, "9047bb48decafbad", "Paper B", "beta")

        prepared = prepare_ask_session(
            db,
            cfg,
            question="Explain MLA projection",
            explicit_paper_ids=["9047bb"],
            channel="cli",
        )
        assert prepared["ok"] is False
        assert "Ambiguous --paper-id prefix match" in str(prepared["message"])
        candidates = prepared.get("candidates", [])
        assert isinstance(candidates, list)
        assert len(candidates) >= 2
        db.close()


def test_unresolved_non_explicit_scope_falls_back_to_retrieval() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = _cfg(root)
        db = PaperDB(cfg.db_path)
        db.initialize()
        _seed_paper(
            db,
            root,
            "mla1",
            "DeepSeek-V2",
            "Multi-head Latent Attention uses down-projection and up-projection matrices for KV compression.",
        )
        _seed_paper(
            db,
            root,
            "noise1",
            "Random Systems Paper",
            "Compiler scheduling and kernel launch details.",
        )
        scope = resolve_question_scope(
            db,
            "In Multi-head Latent Attention, explain down-projection and up-projection for compression.",
        )
        assert scope.selected_paper_ids == []
        assert scope.ambiguous is True

        prepared = prepare_ask_session(
            db,
            cfg,
            question="In Multi-head Latent Attention, explain down-projection and up-projection for compression.",
            top_k=6,
            channel="cli",
        )
        assert prepared["ok"] is True
        assert "mla1" in prepared["paper_ids"]
        selected = prepared.get("selected_papers", [])
        assert isinstance(selected, list)
        assert selected
        assert all(row["reason"] == "retrieval_fallback" for row in selected)
        db.close()


def test_unresolved_explicit_scope_does_not_fallback() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = _cfg(root)
        db = PaperDB(cfg.db_path)
        db.initialize()
        _seed_paper(
            db,
            root,
            "mla1",
            "DeepSeek-V2",
            "Multi-head Latent Attention uses down-projection and up-projection matrices for KV compression.",
        )

        prepared = prepare_ask_session(
            db,
            cfg,
            question="In Multi-head Latent Attention, explain down-projection and up-projection for compression.",
            explicit_paper_ids=["deadbeef"],
            channel="cli",
        )
        assert prepared["ok"] is False
        assert "Could not find paper for --paper-id" in str(prepared["message"])
        assert prepared.get("candidates") == []
        db.close()


def test_prepare_ask_session_returns_full_paper_context_after_routing() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = _cfg(root)
        db = PaperDB(cfg.db_path)
        db.initialize()
        long_text = (
            "Architecture overview for MLA routing and projection behavior. "
            + ("filler " * 700)
            + "TAIL_MARKER_FOR_FULL_CONTEXT"
        )
        _seed_paper(
            db,
            root,
            "mla-full",
            "DeepSeek-V2",
            long_text,
        )
        prepared = prepare_ask_session(
            db,
            cfg,
            question="Explain architecture choices in MLA.",
            explicit_paper_ids=["mla-full"],
            channel="cli",
        )
        assert prepared["ok"] is True
        sources = prepared["sources"]
        assert isinstance(sources, list)
        assert len(sources) == 1
        snippet = str(sources[0]["snippet"])
        assert "TAIL_MARKER_FOR_FULL_CONTEXT" in snippet
        assert len(snippet) > 2000
        db.close()


def test_confirm_ask_session_logs_once_and_is_idempotent() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = _cfg(root)
        db = PaperDB(cfg.db_path)
        db.initialize()
        _seed_paper(
            db,
            root,
            "fa1",
            "FlashAttention: Fast and Memory-Efficient Exact Attention",
            "FlashAttention is an IO-aware exact attention algorithm.",
        )

        prepared = prepare_ask_session(
            db,
            cfg,
            question="Summarize FlashAttention",
            explicit_paper_ids=["fa1"],
            channel="cli",
        )
        assert prepared["ok"] is True
        pending_id = str(prepared["pending_id"])

        first = confirm_ask_session(db, cfg, pending_id=pending_id, approve=True)
        assert first["ok"] is True
        assert first["status"] == "confirmed"
        assert len(db.recent_qa(limit=10)) == 1

        second = confirm_ask_session(db, cfg, pending_id=pending_id, approve=True)
        assert second["ok"] is True
        assert second["status"] == "already_confirmed"
        assert len(db.recent_qa(limit=10)) == 1
        db.close()


def test_confirm_ask_session_uses_answer_override_for_logs() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = _cfg(root)
        db = PaperDB(cfg.db_path)
        db.initialize()
        _seed_paper(
            db,
            root,
            "fa1",
            "FlashAttention: Fast and Memory-Efficient Exact Attention",
            "FlashAttention is an IO-aware exact attention algorithm.",
        )

        prepared = prepare_ask_session(
            db,
            cfg,
            question="Summarize FlashAttention",
            explicit_paper_ids=["fa1"],
            channel="cli",
        )
        assert prepared["ok"] is True
        pending_id = str(prepared["pending_id"])
        final_answer = "Final answer from agent: FlashAttention tiles attention to reduce HBM traffic."

        result = confirm_ask_session(
            db,
            cfg,
            pending_id=pending_id,
            approve=True,
            answer_override=final_answer,
        )
        assert result["ok"] is True
        assert result["status"] == "confirmed"
        assert final_answer in str(result["answer"])

        rows = db.recent_qa(limit=1)
        assert len(rows) == 1
        assert "Final answer from agent" in str(rows[0]["answer"])
        db.close()


def test_commit_or_confirm_propagates_answer_override() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = _cfg(root)
        db = PaperDB(cfg.db_path)
        db.initialize()
        _seed_paper(
            db,
            root,
            "fa1",
            "FlashAttention: Fast and Memory-Efficient Exact Attention",
            "FlashAttention is an IO-aware exact attention algorithm.",
        )
        prepared = prepare_ask_session(
            db,
            cfg,
            question="Summarize FlashAttention",
            explicit_paper_ids=["fa1"],
            channel="mcp",
        )
        pending_id = str(prepared["pending_id"])
        result = commit_or_confirm(
            db,
            cfg,
            pending_id=pending_id,
            approve=True,
            answer_override="Final MCP answer override",
            session_id="sess-mcp",
            confirm_mode="session",
            channel="mcp",
        )
        assert result["ok"] is True
        rows = db.recent_qa(limit=1)
        assert len(rows) == 1
        assert "Final MCP answer override" in str(rows[0]["answer"])
        db.close()


def test_reject_ask_session_does_not_log_qa() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = _cfg(root)
        db = PaperDB(cfg.db_path)
        db.initialize()
        _seed_paper(
            db,
            root,
            "fa1",
            "FlashAttention: Fast and Memory-Efficient Exact Attention",
            "FlashAttention is an IO-aware exact attention algorithm.",
        )

        prepared = prepare_ask_session(
            db,
            cfg,
            question="Summarize FlashAttention",
            explicit_paper_ids=["fa1"],
            channel="cli",
        )
        assert prepared["ok"] is True
        pending_id = str(prepared["pending_id"])

        rejected = confirm_ask_session(db, cfg, pending_id=pending_id, approve=False)
        assert rejected["ok"] is True
        assert rejected["status"] == "rejected"
        assert len(db.recent_qa(limit=10)) == 0
        db.close()


def test_pending_session_expires_before_confirm() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = _cfg(root)
        db = PaperDB(cfg.db_path)
        db.initialize()
        _seed_paper(
            db,
            root,
            "fa1",
            "FlashAttention: Fast and Memory-Efficient Exact Attention",
            "FlashAttention is an IO-aware exact attention algorithm.",
        )
        prepared = prepare_ask_session(
            db,
            cfg,
            question="Summarize FlashAttention",
            explicit_paper_ids=["fa1"],
            channel="cli",
        )
        assert prepared["ok"] is True
        pending_id = str(prepared["pending_id"])
        db.conn.execute("UPDATE pending_ask_sessions SET expires_at = '1970-01-01T00:00:00+00:00' WHERE id = ?", (pending_id,))
        db.conn.commit()
        expired = confirm_ask_session(db, cfg, pending_id=pending_id, approve=True)
        assert expired["ok"] is False
        assert expired["status"] == "expired"
        assert len(db.recent_qa(limit=10)) == 0
        db.close()


def test_cli_ask_requires_confirmation_and_honors_yes_no() -> None:
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
            "fa1",
            "FlashAttention: Fast and Memory-Efficient Exact Attention",
            "FlashAttention is an IO-aware exact attention algorithm.",
        )
        db.close()

        prev = Path.cwd()
        try:
            os.chdir(root)

            denied = runner.invoke(
                app,
                ["ask", "What is FlashAttention?", "--confirm", "no"],
                catch_exceptions=False,
            )
            assert denied.exit_code == 0
            assert "Skipped logging" in denied.stdout
            check_db = PaperDB(cfg.db_path)
            check_db.initialize()
            assert len(check_db.recent_qa(limit=10)) == 0
            check_db.close()

            approved = runner.invoke(
                app,
                ["ask", "What is FlashAttention?", "--confirm", "yes"],
                catch_exceptions=False,
            )
            assert approved.exit_code == 0
            check_db2 = PaperDB(cfg.db_path)
            check_db2.initialize()
            assert len(check_db2.recent_qa(limit=10)) == 1
            check_db2.close()
        finally:
            os.chdir(prev)


def test_scope_hash_order_independent_and_lock_refresh() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = _cfg(root)
        db = PaperDB(cfg.db_path)
        db.initialize()
        a = db.scope_hash_for_papers(["p2", "p1", "p1"])
        b = db.scope_hash_for_papers(["p1", "p2"])
        assert a == b
        lock = db.upsert_scope_lock("sess1", "mcp", ["p1", "p2"], ttl_seconds=120)
        assert lock["session_id"] == "sess1"
        refreshed = db.refresh_scope_lock("sess1", "mcp", ttl_seconds=240)
        assert refreshed is not None
        status = get_scope_lock_status(db, session_id="sess1", channel="mcp")
        assert status["ok"] is True
        assert set(status["paper_ids"]) == {"p1", "p2"}
        db.close()


def test_prepare_with_lock_auto_commit_when_scope_matches() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = _cfg(root)
        db = PaperDB(cfg.db_path)
        db.initialize()
        _seed_paper(db, root, "fa1", "FlashAttention", "flash attention io aware")

        first = prepare_ask_with_lock(
            db,
            cfg,
            question="Summarize FlashAttention",
            explicit_paper_ids=["fa1"],
            channel="mcp",
            session_id="s-lock",
            confirm_mode="session",
        )
        assert first["ok"] is True
        assert first["auto_commit_eligible"] is False
        committed = commit_or_confirm(
            db,
            cfg,
            pending_id=str(first["pending_id"]),
            approve=True,
            session_id="s-lock",
            confirm_mode="session",
            channel="mcp",
        )
        assert committed["ok"] is True
        assert len(db.recent_qa(limit=10)) == 1

        second = prepare_ask_with_lock(
            db,
            cfg,
            question="Summarize FlashAttention",
            explicit_paper_ids=["fa1"],
            channel="mcp",
            session_id="s-lock",
            confirm_mode="session",
        )
        assert second["ok"] is True
        assert second["auto_commit_eligible"] is True
        auto_commit = commit_or_confirm(
            db,
            cfg,
            pending_id=str(second["pending_id"]),
            approve=True,
            session_id="s-lock",
            confirm_mode="session",
            channel="mcp",
        )
        assert auto_commit["ok"] is True
        assert len(db.recent_qa(limit=10)) == 2
        db.close()


def test_prepare_with_lock_scope_change_requires_reconfirm() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = _cfg(root)
        db = PaperDB(cfg.db_path)
        db.initialize()
        _seed_paper(db, root, "fa1", "FlashAttention", "flash attention io aware")
        _seed_paper(db, root, "fa2", "FlashAttention-2", "flash attention 2 better parallelism")

        first = prepare_ask_with_lock(
            db,
            cfg,
            question="Summarize FlashAttention",
            explicit_paper_ids=["fa1"],
            channel="mcp",
            session_id="s-lock",
            confirm_mode="session",
        )
        commit_or_confirm(
            db,
            cfg,
            pending_id=str(first["pending_id"]),
            approve=True,
            session_id="s-lock",
            confirm_mode="session",
            channel="mcp",
        )
        changed = prepare_ask_with_lock(
            db,
            cfg,
            question="Summarize FlashAttention-2",
            explicit_paper_ids=["fa2"],
            channel="mcp",
            session_id="s-lock",
            confirm_mode="session",
        )
        assert changed["ok"] is True
        assert changed["scope_changed"] is True
        assert changed["auto_commit_eligible"] is False
        assert changed["requires_confirmation"] is True
        assert changed["previous_paper_ids"] == ["fa1"]
        assert changed["new_paper_ids"] == ["fa2"]
        db.close()


def test_session_mode_without_session_id_falls_back_to_one_shot() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = _cfg(root)
        db = PaperDB(cfg.db_path)
        db.initialize()
        _seed_paper(db, root, "fa1", "FlashAttention", "flash attention io aware")
        prepared = prepare_ask_with_lock(
            db,
            cfg,
            question="Summarize FlashAttention",
            explicit_paper_ids=["fa1"],
            channel="mcp",
            session_id=None,
            confirm_mode="session",
        )
        assert prepared["ok"] is True
        assert prepared["requires_confirmation"] is True
        assert prepared["auto_commit_eligible"] is False
        assert prepared["reason"] == "session_mode_without_session_id"
        db.close()


def test_cli_session_auto_commit_and_scope_drift_reprompt() -> None:
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = _cfg(root)
        dump_config(cfg, root / "papertool.toml")
        db = PaperDB(cfg.db_path)
        db.initialize()
        _seed_paper(db, root, "fa1", "FlashAttention", "flash attention io aware")
        _seed_paper(db, root, "fa2", "FlashAttention-2", "flash attention 2 better parallelism")
        db.close()

        prev = Path.cwd()
        try:
            os.chdir(root)
            first = runner.invoke(
                app,
                ["ask", "Summarize FlashAttention", "--paper-id", "fa1", "--confirm", "yes"],
                catch_exceptions=False,
            )
            assert first.exit_code == 0

            second = runner.invoke(
                app,
                ["ask", "Summarize FlashAttention", "--paper-id", "fa1"],
                catch_exceptions=False,
            )
            assert second.exit_code == 0
            assert "Auto-logged ask for session" in second.stdout

            drift = runner.invoke(
                app,
                ["ask", "Summarize FlashAttention-2", "--paper-id", "fa2"],
                catch_exceptions=False,
            )
            assert drift.exit_code != 0
            verify = PaperDB(cfg.db_path)
            verify.initialize()
            assert len(verify.recent_qa(limit=10)) == 2
            verify.close()
        finally:
            os.chdir(prev)
