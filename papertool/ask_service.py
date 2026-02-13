from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from papertool.config import PaperToolConfig
from papertool.db import PaperDB
from papertool.models import SearchHit
from papertool.obsidian import (
    append_qa_to_daily_note,
    append_qa_to_paper_note,
    sync_review_prompts_in_paper_note,
    upsert_paper_note,
)
from papertool.query_scope import resolve_question_scope
from papertool.retrieval import hits_to_dict, retrieve, synthesize_answer

VALID_CONFIRM_MODES = {"session", "always", "never"}


def _sanitize_snippet(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").replace("[", "").replace("]", "")).strip()


def _takeaway_from_snippets(snippets: list[str], fallback: str) -> str:
    cleaned = [_sanitize_snippet(snippet) for snippet in snippets if _sanitize_snippet(snippet)]
    if not cleaned:
        return fallback
    return " ".join(cleaned[:2])[:500]


def _hits_from_json(payload: str) -> list[SearchHit]:
    data = json.loads(payload or "[]")
    hits: list[SearchHit] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        try:
            hits.append(
                SearchHit(
                    paper_id=str(row["paper_id"]),
                    title=str(row["title"]),
                    path=str(row["path"]),
                    snippet=str(row["snippet"]),
                    score=float(row["score"]),
                )
            )
        except Exception:
            continue
    return hits


def _normalize_confirm_mode(value: str | None, default: str = "session") -> str:
    mode = (value or default or "session").strip().lower()
    if mode not in VALID_CONFIRM_MODES:
        raise ValueError(f"Invalid confirm mode: {mode}")
    return mode


def _prepare_ask_core(
    db: PaperDB,
    config: PaperToolConfig,
    *,
    question: str,
    top_k: int = 6,
    topic: str | None = None,
    community_id: str | None = None,
    explicit_paper_ids: list[str] | None = None,
    explicit_arxiv_ids: list[str] | None = None,
) -> dict[str, Any]:
    scope = resolve_question_scope(
        db,
        question,
        explicit_paper_ids=explicit_paper_ids or [],
        explicit_arxiv_ids=explicit_arxiv_ids or [],
    )
    if not scope.selected_paper_ids:
        return {
            "ok": False,
            "error": "ambiguous_scope",
            "message": scope.message,
            "candidates": [asdict(item) for item in scope.candidates],
        }

    hits = retrieve(
        db,
        question,
        top_k=top_k,
        topic=topic,
        community_id=community_id,
        config=config,
        paper_ids=scope.selected_paper_ids,
    )
    answer = synthesize_answer(question, hits)
    return {
        "ok": True,
        "question": question,
        "topic": topic,
        "community_id": community_id,
        "paper_ids": list(scope.selected_paper_ids),
        "selected_papers": [asdict(item) for item in scope.selected_papers],
        "answer_preview": answer,
        "sources": hits_to_dict(hits),
    }


def _paper_ids_from_row_json(payload: str) -> list[str]:
    return [str(item) for item in json.loads(payload or "[]") if str(item).strip()]


def prepare_ask_session(
    db: PaperDB,
    config: PaperToolConfig,
    *,
    question: str,
    top_k: int = 6,
    topic: str | None = None,
    community_id: str | None = None,
    explicit_paper_ids: list[str] | None = None,
    explicit_arxiv_ids: list[str] | None = None,
    channel: str = "cli",
) -> dict[str, Any]:
    prepared = _prepare_ask_core(
        db,
        config,
        question=question,
        top_k=top_k,
        topic=topic,
        community_id=community_id,
        explicit_paper_ids=explicit_paper_ids,
        explicit_arxiv_ids=explicit_arxiv_ids,
    )
    if not prepared.get("ok", False):
        return prepared

    pending_id = db.create_pending_ask_session(
        channel=channel,
        question=str(prepared["question"]),
        answer_preview=str(prepared["answer_preview"]),
        hits_json=json.dumps(prepared["sources"], ensure_ascii=True),
        paper_ids=[str(item) for item in prepared["paper_ids"]],
        ttl_seconds=int(config.ask_session_ttl_sec),
    )
    prepared["pending_id"] = pending_id
    prepared["confirm_mode"] = "always"
    prepared["requires_confirmation"] = True
    prepared["auto_commit_eligible"] = False
    return prepared


def prepare_ask_with_lock(
    db: PaperDB,
    config: PaperToolConfig,
    *,
    question: str,
    top_k: int = 6,
    topic: str | None = None,
    community_id: str | None = None,
    explicit_paper_ids: list[str] | None = None,
    explicit_arxiv_ids: list[str] | None = None,
    channel: str = "cli",
    session_id: str | None = None,
    confirm_mode: str | None = None,
) -> dict[str, Any]:
    mode = _normalize_confirm_mode(confirm_mode, default=config.ask_confirmation_mode)
    prepared = _prepare_ask_core(
        db,
        config,
        question=question,
        top_k=top_k,
        topic=topic,
        community_id=community_id,
        explicit_paper_ids=explicit_paper_ids,
        explicit_arxiv_ids=explicit_arxiv_ids,
    )
    if not prepared.get("ok", False):
        prepared["confirm_mode"] = mode
        return prepared

    current_paper_ids = [str(item) for item in prepared["paper_ids"]]
    current_scope_hash = db.scope_hash_for_papers(current_paper_ids)
    lock = db.get_scope_lock(session_id, channel) if (mode == "session" and session_id) else None
    previous_paper_ids: list[str] = []
    scope_changed = False
    lock_matched = False
    if lock:
        previous_paper_ids = _paper_ids_from_row_json(str(lock["paper_ids_json"]))
        previous_hash = db.scope_hash_for_papers(previous_paper_ids)
        lock_matched = previous_hash == current_scope_hash
        scope_changed = not lock_matched

    requires_confirmation = True
    auto_commit_eligible = False
    reason = "manual_confirmation_required"
    if mode == "never":
        requires_confirmation = False
        auto_commit_eligible = True
        reason = "confirm_mode_never"
    elif mode == "session":
        if not session_id:
            reason = "session_mode_without_session_id"
        elif lock_matched:
            requires_confirmation = False
            auto_commit_eligible = True
            reason = "scope_lock_match"
            db.refresh_scope_lock(session_id, channel, ttl_seconds=int(config.ask_session_ttl_sec))
        elif scope_changed:
            reason = "scope_changed_requires_confirmation"
        else:
            reason = "new_session_scope_requires_confirmation"

    pending_id = db.create_pending_ask_session(
        channel=channel,
        question=str(prepared["question"]),
        answer_preview=str(prepared["answer_preview"]),
        hits_json=json.dumps(prepared["sources"], ensure_ascii=True),
        paper_ids=current_paper_ids,
        ttl_seconds=int(config.ask_session_ttl_sec),
    )
    prepared.update(
        {
            "pending_id": pending_id,
            "confirm_mode": mode,
            "session_id": session_id,
            "scope_hash": current_scope_hash,
            "requires_confirmation": requires_confirmation,
            "auto_commit_eligible": auto_commit_eligible,
            "scope_changed": scope_changed,
            "previous_paper_ids": previous_paper_ids,
            "new_paper_ids": current_paper_ids,
            "reason": reason,
        }
    )
    return prepared


def get_scope_lock_status(db: PaperDB, *, session_id: str, channel: str) -> dict[str, Any]:
    row = db.get_scope_lock(session_id, channel, include_expired=True)
    if not row:
        return {
            "ok": False,
            "session_id": session_id,
            "channel": channel,
            "error": "scope_lock_not_found",
        }
    expires_at = str(row["expires_at"])
    expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    active = expires >= datetime.now(timezone.utc)
    return {
        "ok": True,
        "session_id": session_id,
        "channel": channel,
        "paper_ids": _paper_ids_from_row_json(str(row["paper_ids_json"])),
        "scope_hash": str(row["scope_hash"]),
        "confirmed_at": str(row["confirmed_at"]),
        "last_used_at": str(row["last_used_at"]),
        "expires_at": expires_at,
        "active": active,
    }


def commit_confirmed_ask(
    db: PaperDB,
    config: PaperToolConfig,
    *,
    pending_id: str,
    save_notes: bool = True,
) -> dict[str, Any]:
    session = db.get_pending_ask_session(pending_id, include_expired=True)
    if not session:
        return {"ok": False, "status": "not_found", "pending_id": pending_id}

    status = str(session["status"])
    if status == "confirmed":
        return {"ok": True, "status": "already_confirmed", "pending_id": pending_id}
    if status == "rejected":
        return {"ok": False, "status": "already_rejected", "pending_id": pending_id}
    if status == "expired":
        return {"ok": False, "status": "expired", "pending_id": pending_id}
    if status != "pending":
        return {"ok": False, "status": f"invalid_status:{status}", "pending_id": pending_id}

    question = str(session["question"])
    answer = str(session["answer_preview"])
    paper_ids = [str(item) for item in json.loads(str(session["paper_ids_json"]) or "[]")]
    hits = _hits_from_json(str(session["hits_json"]))

    db.log_qa(question, answer, paper_ids=paper_ids, channel=str(session["channel"]))

    notes_written: list[str] = []
    if save_notes and config.obsidian_vault:
        paper_titles: list[str] = []
        for paper_id in paper_ids:
            paper = db.get_paper(paper_id)
            if not paper:
                continue
            paper_titles.append(str(paper["title"]))
            paper_snippets = [hit.snippet for hit in hits if hit.paper_id == paper_id][:3]
            takeaway = _takeaway_from_snippets(paper_snippets, answer)
            upsert_paper_note(
                config,
                title=paper["title"],
                source_path=paper["path"],
                summary=paper["summary"] or "",
                doi=paper["doi"],
                arxiv_id=paper["arxiv_id"],
            )
            note = append_qa_to_paper_note(
                config,
                title=paper["title"],
                question=question,
                answer=takeaway,
                evidence_snippets=paper_snippets,
            )
            notes_written.append(str(note))
            prompts = [str(row["question_text"]) for row in db.quiz_prompts_for_paper(str(paper_id), limit=50)]
            sync_review_prompts_in_paper_note(
                config,
                title=paper["title"],
                prompts=prompts,
            )

        if paper_titles:
            daily = append_qa_to_daily_note(
                config,
                question=question,
                answer=answer,
                paper_titles=paper_titles,
            )
            notes_written.append(str(daily))

    db.mark_pending_ask_session_status(pending_id, "confirmed")
    return {
        "ok": True,
        "status": "confirmed",
        "pending_id": pending_id,
        "question": question,
        "answer": answer,
        "paper_ids": paper_ids,
        "notes_written": notes_written,
    }


def commit_or_confirm(
    db: PaperDB,
    config: PaperToolConfig,
    *,
    pending_id: str,
    approve: bool,
    save_notes: bool = True,
    session_id: str | None = None,
    confirm_mode: str | None = None,
    channel: str = "cli",
) -> dict[str, Any]:
    mode = _normalize_confirm_mode(confirm_mode, default=config.ask_confirmation_mode)
    result = confirm_ask_session(
        db,
        config,
        pending_id=pending_id,
        approve=approve,
        save_notes=save_notes,
    )
    if not approve or not result.get("ok", False):
        return result

    if mode != "session" or not session_id:
        return result

    paper_ids = result.get("paper_ids")
    normalized_paper_ids = [str(item) for item in paper_ids] if isinstance(paper_ids, list) else []
    if not normalized_paper_ids:
        row = db.get_pending_ask_session(pending_id, include_expired=True)
        if row:
            normalized_paper_ids = _paper_ids_from_row_json(str(row["paper_ids_json"]))
    if normalized_paper_ids:
        lock = db.upsert_scope_lock(
            session_id=session_id,
            channel=channel,
            paper_ids=normalized_paper_ids,
            ttl_seconds=int(config.ask_session_ttl_sec),
        )
        result["scope_lock"] = lock
    return result


def confirm_ask_session(
    db: PaperDB,
    config: PaperToolConfig,
    *,
    pending_id: str,
    approve: bool,
    save_notes: bool = True,
) -> dict[str, Any]:
    if not approve:
        session = db.get_pending_ask_session(pending_id, include_expired=True)
        if not session:
            return {"ok": False, "status": "not_found", "pending_id": pending_id}
        status = str(session["status"])
        if status == "confirmed":
            return {"ok": True, "status": "already_confirmed", "pending_id": pending_id}
        if status == "rejected":
            return {"ok": True, "status": "already_rejected", "pending_id": pending_id}
        if status == "expired":
            return {"ok": True, "status": "expired", "pending_id": pending_id}
        db.mark_pending_ask_session_status(pending_id, "rejected")
        return {"ok": True, "status": "rejected", "pending_id": pending_id}
    return commit_confirmed_ask(db, config, pending_id=pending_id, save_notes=save_notes)
