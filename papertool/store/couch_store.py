from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from papertool.config import PaperToolConfig
from papertool.couch_client import CouchClient
from papertool.db import PaperDB


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CouchStore:
    def __init__(self, config: PaperToolConfig) -> None:
        self.config = config
        self.db = PaperDB(config.db_path)
        self._couch: CouchClient | None = None
        if config.couchdb_url:
            self._couch = CouchClient(config.couchdb_url)

    def initialize(self) -> None:
        self.db.initialize()
        if not self._couch:
            return
        self._couch.ensure_db(self.config.couchdb_db_meta)
        self._couch.ensure_db(self.config.couchdb_db_events)
        self._couch.ensure_db(self.config.couchdb_db_jobs)
        if self.config.sync_enabled:
            try:
                self.sync_run(pull=True, push=False)
            except Exception as exc:
                self.db.set_sync_state("last_sync_error", str(exc))

    def close(self) -> None:
        if self.config.sync_enabled and self._couch:
            try:
                self.sync_run(pull=False, push=True)
            except Exception as exc:
                self.db.set_sync_state("last_sync_error", str(exc))
        self.db.close()

    def _table_columns(self, table: str) -> list[str]:
        rows = self.db.conn.execute(f"PRAGMA table_info({table})").fetchall()
        return [str(row["name"]) for row in rows]

    def _table_rows(self, table: str, *, where: str | None = None, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        sql = f"SELECT * FROM {table}"
        if where:
            sql += f" WHERE {where}"
        rows = self.db.conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def _docs_from_local(self) -> list[dict[str, Any]]:
        docs: list[dict[str, Any]] = []

        for row in self._table_rows("papers"):
            doc_id = f"paper:{row['id']}"
            docs.append({"_id": doc_id, "type": "paper", "updated_at": row.get("ingested_at") or _utc_now_iso(), "data": row})

        for row in self._table_rows("chunks"):
            doc_id = f"chunk:{row['id']}"
            docs.append({"_id": doc_id, "type": "chunk", "updated_at": _utc_now_iso(), "data": row})

        for row in self._table_rows("citations"):
            doc_id = f"citation:{row['source_paper_id']}:{row['target_paper_id']}"
            docs.append({"_id": doc_id, "type": "citation_edge", "updated_at": _utc_now_iso(), "data": row})

        for row in self._table_rows("reading_queue"):
            doc_id = f"queue:{row['paper_id']}"
            docs.append({"_id": doc_id, "type": "queue_entry", "updated_at": row.get("updated_at") or _utc_now_iso(), "data": row})

        for row in self._table_rows("quiz_history"):
            doc_id = f"quiz:{row['id']}"
            docs.append({"_id": doc_id, "type": "quiz_entry", "updated_at": row.get("created_at") or _utc_now_iso(), "data": row})

        for row in self._table_rows("review_cards"):
            doc_id = f"review:{row['id']}"
            docs.append({"_id": doc_id, "type": "review_card", "updated_at": row.get("updated_at") or _utc_now_iso(), "data": row})

        for row in self._table_rows("paper_topic_scores"):
            doc_id = f"topic_score:{row['paper_id']}:{row['topic_id']}"
            docs.append({"_id": doc_id, "type": "topic_score", "updated_at": row.get("updated_at") or _utc_now_iso(), "data": row})

        for row in self._table_rows("citation_communities"):
            doc_id = f"citation_community:{row['paper_id']}"
            docs.append({"_id": doc_id, "type": "citation_community", "updated_at": row.get("updated_at") or _utc_now_iso(), "data": row})

        for row in self._table_rows("qa_log"):
            doc_id = f"qa_log:{row['id']}"
            docs.append({"_id": doc_id, "type": "qa_log", "updated_at": row.get("asked_at") or _utc_now_iso(), "data": row})

        for row in self._table_rows("topic_catalog"):
            doc_id = f"topic_catalog:{row['topic_id']}"
            docs.append({"_id": doc_id, "type": "topic_catalog", "updated_at": row.get("created_at") or _utc_now_iso(), "data": row})

        return docs

    def _insert_row(self, table: str, data: dict[str, Any], columns: list[str]) -> None:
        payload = {key: data.get(key) for key in columns if key in data}
        if not payload:
            return
        keys = list(payload.keys())
        placeholders = ",".join("?" for _ in keys)
        cols = ",".join(keys)
        sql = f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({placeholders})"
        self.db.conn.execute(sql, [payload[key] for key in keys])

    def _apply_remote_docs(self, docs: list[dict[str, Any]]) -> dict[str, int]:
        by_type: dict[str, list[dict[str, Any]]] = {}
        for doc in docs:
            if doc.get("_id", "").startswith("_"):
                continue
            kind = str(doc.get("type") or "")
            data = doc.get("data")
            if not kind or not isinstance(data, dict):
                continue
            by_type.setdefault(kind, []).append(data)

        stats: dict[str, int] = {}
        self.db.conn.execute("PRAGMA foreign_keys = OFF")
        try:
            if by_type.get("paper"):
                self.db.conn.execute("DELETE FROM papers")
                cols = self._table_columns("papers")
                for data in by_type["paper"]:
                    self._insert_row("papers", data, cols)
                stats["papers"] = len(by_type["paper"])

            if by_type.get("queue_entry"):
                self.db.conn.execute("DELETE FROM reading_queue")
                cols = self._table_columns("reading_queue")
                for data in by_type["queue_entry"]:
                    self._insert_row("reading_queue", data, cols)
                stats["reading_queue"] = len(by_type["queue_entry"])

            if by_type.get("citation_edge"):
                self.db.conn.execute("DELETE FROM citations")
                cols = self._table_columns("citations")
                for data in by_type["citation_edge"]:
                    self._insert_row("citations", data, cols)
                stats["citations"] = len(by_type["citation_edge"])

            if by_type.get("chunk"):
                self.db.conn.execute("DELETE FROM chunk_fts")
                self.db.conn.execute("DELETE FROM chunks")
                cols = self._table_columns("chunks")
                for data in by_type["chunk"]:
                    self._insert_row("chunks", data, cols)
                    if "id" in data and "content" in data and "paper_id" in data:
                        self.db.conn.execute(
                            "INSERT INTO chunk_fts(rowid, content, paper_id) VALUES(?, ?, ?)",
                            (data["id"], data["content"], data["paper_id"]),
                        )
                stats["chunks"] = len(by_type["chunk"])

            if by_type.get("quiz_entry"):
                self.db.conn.execute("DELETE FROM quiz_history")
                cols = self._table_columns("quiz_history")
                for data in by_type["quiz_entry"]:
                    self._insert_row("quiz_history", data, cols)
                stats["quiz_history"] = len(by_type["quiz_entry"])

            if by_type.get("review_card"):
                self.db.conn.execute("DELETE FROM review_cards")
                cols = self._table_columns("review_cards")
                for data in by_type["review_card"]:
                    self._insert_row("review_cards", data, cols)
                stats["review_cards"] = len(by_type["review_card"])

            if by_type.get("topic_catalog"):
                self.db.conn.execute("DELETE FROM topic_catalog")
                cols = self._table_columns("topic_catalog")
                for data in by_type["topic_catalog"]:
                    self._insert_row("topic_catalog", data, cols)
                stats["topic_catalog"] = len(by_type["topic_catalog"])

            if by_type.get("topic_score"):
                self.db.conn.execute("DELETE FROM paper_topic_scores")
                cols = self._table_columns("paper_topic_scores")
                for data in by_type["topic_score"]:
                    self._insert_row("paper_topic_scores", data, cols)
                stats["paper_topic_scores"] = len(by_type["topic_score"])

            if by_type.get("citation_community"):
                self.db.conn.execute("DELETE FROM citation_communities")
                cols = self._table_columns("citation_communities")
                for data in by_type["citation_community"]:
                    self._insert_row("citation_communities", data, cols)
                stats["citation_communities"] = len(by_type["citation_community"])

            if by_type.get("qa_log"):
                self.db.conn.execute("DELETE FROM qa_log")
                cols = self._table_columns("qa_log")
                for data in by_type["qa_log"]:
                    self._insert_row("qa_log", data, cols)
                stats["qa_log"] = len(by_type["qa_log"])

            self.db.conn.commit()
        finally:
            self.db.conn.execute("PRAGMA foreign_keys = ON")

        return stats

    def sync_run(self, *, pull: bool = True, push: bool = True) -> dict[str, Any]:
        if not self._couch:
            return {
                "ok": False,
                "backend": "couch",
                "error": "couchdb_url is not configured",
            }

        result: dict[str, Any] = {
            "ok": True,
            "backend": self.config.storage_backend,
            "push": {},
            "pull": {},
        }

        if push:
            docs = self._docs_from_local()
            for doc in docs:
                self._couch.upsert_doc(self.config.couchdb_db_meta, str(doc["_id"]), doc)
            event_doc = {
                "type": "sync_event",
                "updated_at": _utc_now_iso(),
                "data": {
                    "source": "local_cache",
                    "doc_count": len(docs),
                    "mode": "push",
                },
            }
            self._couch.upsert_doc(
                self.config.couchdb_db_events,
                f"event:{uuid.uuid4()}",
                event_doc,
            )
            self.db.set_sync_state("last_push_at", _utc_now_iso())
            self.db.set_sync_state("last_push_count", str(len(docs)))
            result["push"] = {"doc_count": len(docs)}

        if pull:
            all_rows = self._couch.all_docs(self.config.couchdb_db_meta, include_docs=True)
            docs = [row.get("doc") for row in all_rows if isinstance(row.get("doc"), dict)]
            stats = self._apply_remote_docs([doc for doc in docs if isinstance(doc, dict)])
            self.db.set_sync_state("last_pull_at", _utc_now_iso())
            self.db.set_sync_state("last_pull_count", str(len(docs)))
            result["pull"] = {"doc_count": len(docs), "applied": stats}

        self.db.set_sync_state("last_sync_error", "")
        return result

    def sync_status(self) -> dict[str, Any]:
        return {
            "backend": self.config.storage_backend,
            "sync_enabled": self.config.sync_enabled,
            "state": [dict(row) for row in self.db.sync_state_all()],
        }

    def remote_health(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "backend": self.config.storage_backend,
            "couch": {"ok": False},
            "api": {"ok": False},
        }

        if self._couch:
            try:
                payload = self._couch.health()
                out["couch"] = {"ok": True, "payload": payload}
            except Exception as exc:
                out["couch"] = {"ok": False, "error": str(exc)}

        base_url = (self.config.remote_api_base_url or "").strip().rstrip("/")
        if base_url:
            headers = {"Accept": "application/json"}
            token = (self.config.remote_api_token or "").strip()
            if token:
                headers["Authorization"] = f"Bearer {token}"
            req = Request(f"{base_url}/v1/health", headers=headers, method="GET")
            try:
                with urlopen(req, timeout=15) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                    out["api"] = {"ok": True, "status": resp.status, "payload": body}
            except HTTPError as exc:
                payload: dict[str, Any] | str = {}
                try:
                    raw = exc.read().decode("utf-8")
                    payload = json.loads(raw) if raw else {}
                except Exception:
                    payload = str(exc)
                out["api"] = {"ok": False, "status": exc.code, "error": str(exc), "payload": payload}
            except (URLError, RuntimeError, ValueError) as exc:
                out["api"] = {"ok": False, "error": str(exc)}

        out["ok"] = bool(out["couch"].get("ok") or out["api"].get("ok"))
        return out
