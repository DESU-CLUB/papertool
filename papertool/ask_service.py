from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from papertool.config import PaperToolConfig
from papertool.db import PaperDB
from papertool.models import SearchHit
from papertool.query_scope import ScopeCandidate, resolve_question_scope
from papertool.retrieval import hits_to_dict, retrieve, synthesize_answer

VALID_CONFIRM_MODES = {"session", "always", "never"}


def _expand_hits_to_full_papers(db: PaperDB, selected_paper_ids: list[str], routed_hits: list[SearchHit]) -> list[SearchHit]:
    if not selected_paper_ids:
        return []
    ordered_ids: list[str] = []
    seen: set[str] = set()
    for paper_id in selected_paper_ids:
        value = str(paper_id or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered_ids.append(value)
    if not ordered_ids:
        return []

    best_by_paper: dict[str, SearchHit] = {}
    for hit in routed_hits:
        current = best_by_paper.get(hit.paper_id)
        if current is None or hit.score > current.score:
            best_by_paper[hit.paper_id] = hit

    full_text_map = db.paper_full_texts(ordered_ids)
    expanded: list[SearchHit] = []
    for paper_id in ordered_ids:
        full_text = full_text_map.get(paper_id)
        if not full_text:
            continue
        routed = best_by_paper.get(paper_id)
        if routed is not None:
            title = routed.title
            path = routed.path
            score = float(routed.score)
        else:
            paper = db.get_paper(paper_id)
            if not paper:
                continue
            title = str(paper["title"])
            path = str(paper["path"])
            score = 0.0
        expanded.append(
            SearchHit(
                paper_id=paper_id,
                title=title,
                path=path,
                snippet=full_text,
                score=score,
            )
        )
    return expanded


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
    explicit_paper_scope = [str(item).strip() for item in (explicit_paper_ids or []) if str(item).strip()]
    explicit_arxiv_scope = [str(item).strip() for item in (explicit_arxiv_ids or []) if str(item).strip()]
    has_explicit_scope = bool(explicit_paper_scope or explicit_arxiv_scope)

    scope = resolve_question_scope(
        db,
        question,
        explicit_paper_ids=explicit_paper_scope,
        explicit_arxiv_ids=explicit_arxiv_scope,
    )
    fallback_hits: list[SearchHit] | None = None
    if not scope.selected_paper_ids and not has_explicit_scope:
        fallback_hits = retrieve(
            db,
            question,
            top_k=max(top_k, 8),
            topic=topic,
            community_id=community_id,
            config=config,
            paper_ids=None,
        )
        selected_ids: list[str] = []
        selected_candidates: list[ScopeCandidate] = []
        seen_ids: set[str] = set()
        for hit in fallback_hits:
            if hit.paper_id in seen_ids:
                continue
            seen_ids.add(hit.paper_id)
            selected_ids.append(hit.paper_id)
            selected_candidates.append(
                ScopeCandidate(
                    paper_id=hit.paper_id,
                    title=hit.title,
                    score=float(hit.score),
                    reason="retrieval_fallback",
                )
            )
            if len(selected_ids) >= 3:
                break
        if selected_ids:
            scope.selected_paper_ids = selected_ids
            scope.selected_papers = selected_candidates
            scope.candidates = selected_candidates
            scope.ambiguous = False
            scope.message = "Resolved scoped papers from retrieval fallback."

    if not scope.selected_paper_ids:
        return {
            "ok": False,
            "error": "ambiguous_scope",
            "message": scope.message,
            "candidates": [asdict(item) for item in scope.candidates],
        }

    if fallback_hits is not None and scope.selected_paper_ids:
        selected_id_set = set(scope.selected_paper_ids)
        scoped_hits = [hit for hit in fallback_hits if hit.paper_id in selected_id_set]
        if scoped_hits:
            hits = scoped_hits[:top_k]
        else:
            hits = retrieve(
                db,
                question,
                top_k=top_k,
                topic=topic,
                community_id=community_id,
                config=config,
                paper_ids=scope.selected_paper_ids,
            )
    else:
        hits = retrieve(
            db,
            question,
            top_k=top_k,
            topic=topic,
            community_id=community_id,
            config=config,
            paper_ids=scope.selected_paper_ids,
        )
    ask_hits = _expand_hits_to_full_papers(db, scope.selected_paper_ids, hits)
    answer = synthesize_answer(question, ask_hits)
    return {
        "ok": True,
        "question": question,
        "topic": topic,
        "community_id": community_id,
        "paper_ids": list(scope.selected_paper_ids),
        "selected_papers": [asdict(item) for item in scope.selected_papers],
        "answer_preview": answer,
        "sources": hits_to_dict(ask_hits),
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
    _config: PaperToolConfig,
    *,
    pending_id: str,
    answer_override: str | None = None,
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
    if answer_override is not None:
        override_clean = str(answer_override).strip()
        if override_clean:
            answer = override_clean
    paper_ids = [str(item) for item in json.loads(str(session["paper_ids_json"]) or "[]")]

    db.log_qa(question, answer, paper_ids=paper_ids, channel=str(session["channel"]))

    db.mark_pending_ask_session_status(pending_id, "confirmed")
    return {
        "ok": True,
        "status": "confirmed",
        "pending_id": pending_id,
        "question": question,
        "answer": answer,
        "paper_ids": paper_ids,
    }


def commit_or_confirm(
    db: PaperDB,
    config: PaperToolConfig,
    *,
    pending_id: str,
    approve: bool,
    answer_override: str | None = None,
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
        answer_override=answer_override,
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
    answer_override: str | None = None,
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
    return commit_confirmed_ask(
        db,
        config,
        pending_id=pending_id,
        answer_override=answer_override,
    )
