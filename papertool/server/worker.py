from __future__ import annotations

import random
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from papertool.config import load_config
from papertool.couch_client import CouchClient
from papertool.store import create_store
from papertool.url_import import import_result_to_dict, import_url_to_library

BACKOFF_STEPS_SEC = [30, 60, 120, 240, 480, 900, 1800]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_ts() -> float:
    return time.time()


def _next_backoff_sec(attempt: int) -> int:
    idx = max(0, min(attempt, len(BACKOFF_STEPS_SEC) - 1))
    base = BACKOFF_STEPS_SEC[idx]
    jitter = max(1, int(base * 0.1))
    return max(1, base + random.randint(-jitter, jitter))


def _emit_event(client: CouchClient, db_name: str, *, entity_type: str, entity_id: str, before: dict[str, Any], after: dict[str, Any]) -> None:
    now = _utc_now_iso()
    event = {
        "_id": f"event:{uuid.uuid4()}",
        "op_id": str(uuid.uuid4()),
        "entity_type": entity_type,
        "entity_id": entity_id,
        "updated_at": now,
        "updated_by": "papertool-worker",
        "payload_before": before,
        "payload_after": after,
    }
    client.upsert_doc(db_name, str(event["_id"]), event)


def _iter_pending_jobs(client: CouchClient, db_name: str, limit: int) -> list[dict[str, Any]]:
    rows = client.all_docs(db_name, include_docs=True, limit=max(limit * 10, 100))
    now = _now_ts()
    pending: list[dict[str, Any]] = []
    for row in rows:
        doc = row.get("doc")
        if not isinstance(doc, dict):
            continue
        if doc.get("type") != "capture_job":
            continue
        status = str(doc.get("status") or "")
        if status not in {"pending", "retry"}:
            continue
        next_retry_at = doc.get("next_retry_at")
        if isinstance(next_retry_at, (int, float)) and next_retry_at > now:
            continue
        pending.append(doc)
    pending.sort(key=lambda d: str(d.get("created_at") or ""))
    return pending[:limit]


def process_capture_jobs_once(limit: int = 10) -> dict[str, Any]:
    cfg = load_config()
    if not cfg.couchdb_url:
        return {"ok": False, "error": "couchdb_url is not configured"}

    client = CouchClient(cfg.couchdb_url)
    client.ensure_db(cfg.couchdb_db_jobs)
    client.ensure_db(cfg.couchdb_db_events)
    jobs = _iter_pending_jobs(client, cfg.couchdb_db_jobs, limit)
    if not jobs:
        return {"ok": True, "processed": 0, "imported": 0, "failed": 0, "retrying": 0}

    store = create_store(cfg)
    store.initialize()
    imported = 0
    failed = 0
    retrying = 0
    processed = 0
    try:
        for job in jobs:
            processed += 1
            before = dict(job)
            attempts = int(job.get("attempts") or 0) + 1
            job["status"] = "processing"
            job["attempts"] = attempts
            job["updated_at"] = _utc_now_iso()
            client.upsert_doc(cfg.couchdb_db_jobs, str(job["_id"]), job)

            payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
            url = str(payload.get("url") or "")
            title = payload.get("title")
            context_text = payload.get("context_text")
            try:
                result = import_url_to_library(
                    store.db,
                    cfg.library_dir,
                    url,
                    page_title=title if isinstance(title, str) else None,
                    context_text=context_text if isinstance(context_text, str) else None,
                )
                job["status"] = "done"
                job["result"] = import_result_to_dict(result)
                job["last_error"] = ""
                job["updated_at"] = _utc_now_iso()
                imported += 1
            except Exception as exc:
                job["last_error"] = str(exc)
                if attempts >= len(BACKOFF_STEPS_SEC):
                    job["status"] = "failed"
                    failed += 1
                else:
                    job["status"] = "retry"
                    job["next_retry_at"] = _now_ts() + _next_backoff_sec(attempts)
                    retrying += 1
                job["updated_at"] = _utc_now_iso()

            client.upsert_doc(cfg.couchdb_db_jobs, str(job["_id"]), job)
            _emit_event(
                client,
                cfg.couchdb_db_events,
                entity_type="capture_job",
                entity_id=str(job["_id"]),
                before=before,
                after=job,
            )
    finally:
        store.close()

    return {
        "ok": True,
        "processed": processed,
        "imported": imported,
        "failed": failed,
        "retrying": retrying,
    }


def run_worker_loop(poll_interval_sec: int = 5) -> None:
    while True:
        result = process_capture_jobs_once(limit=20)
        if not result.get("ok"):
            print(f"worker error: {result.get('error')}")
            time.sleep(max(5, poll_interval_sec))
            continue
        time.sleep(max(1, poll_interval_sec))

