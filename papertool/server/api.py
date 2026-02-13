from __future__ import annotations

import ipaddress
import json
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from papertool.config import load_config
from papertool.couch_client import CouchClient
from papertool.store import create_store
from papertool.url_import import import_result_to_dict, import_url_to_library


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_tailscale_or_local(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if addr.is_loopback:
        return True
    # Docker bridge/NAT setups may surface private source IPs at the app layer.
    if addr.is_private:
        return True
    if isinstance(addr, ipaddress.IPv4Address):
        return addr in ipaddress.ip_network("100.64.0.0/10")
    return addr in ipaddress.ip_network("fd7a:115c:a1e0::/48")


def _unauthorized(handler: BaseHTTPRequestHandler, message: str = "unauthorized") -> None:
    payload = {"ok": False, "error": message}
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(401)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def run_api_server(host: str = "0.0.0.0", port: int = 18443) -> None:
    cfg = load_config()

    class ApiHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def _json(self, status: int, payload: dict[str, object]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,PATCH,OPTIONS")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorize(self) -> bool:
            remote_ip = self.client_address[0] if self.client_address else ""
            if not _is_tailscale_or_local(remote_ip):
                _unauthorized(self, "client_not_allowed")
                return False
            token = (cfg.remote_api_token or "").strip()
            if not token:
                return True
            header = self.headers.get("Authorization") or ""
            if header != f"Bearer {token}":
                _unauthorized(self)
                return False
            return True

        def _read_json(self) -> dict[str, object]:
            size = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(size) if size else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                return {}
            if isinstance(payload, dict):
                return payload
            return {}

        def do_OPTIONS(self) -> None:  # noqa: N802
            self._json(204, {})

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            route = parsed.path.rstrip("/")
            if route == "/v1/health":
                if not self._authorize():
                    return
                status = {
                    "ok": True,
                    "storage_backend": cfg.storage_backend,
                    "couchdb_url": bool(cfg.couchdb_url),
                    "minio_endpoint": bool(cfg.minio_endpoint),
                    "library_dir": str(cfg.library_dir),
                    "db_path": str(cfg.db_path),
                }
                self._json(200, status)
                return

            if route == "/v1/sync/changes":
                if not self._authorize():
                    return
                if not cfg.couchdb_url:
                    self._json(400, {"ok": False, "error": "couchdb_not_configured"})
                    return
                query = parse_qs(parsed.query)
                since = str((query.get("since") or ["0"])[0])
                limit = int((query.get("limit") or ["1000"])[0])
                client = CouchClient(cfg.couchdb_url)
                payload = client.changes(cfg.couchdb_db_meta, since=since, include_docs=True, limit=limit)
                self._json(200, {"ok": True, "changes": payload})
                return

            if route.startswith("/v1/jobs/"):
                if not self._authorize():
                    return
                if not cfg.couchdb_url:
                    self._json(400, {"ok": False, "error": "couchdb_not_configured"})
                    return
                job_id = route.split("/")[-1]
                if not job_id:
                    self._json(400, {"ok": False, "error": "job_id required"})
                    return
                client = CouchClient(cfg.couchdb_url)
                doc = client.get_doc(cfg.couchdb_db_jobs, f"job:{job_id}")
                if not doc:
                    self._json(404, {"ok": False, "error": "job_not_found", "job_id": job_id})
                    return
                self._json(
                    200,
                    {
                        "ok": True,
                        "job_id": job_id,
                        "status": doc.get("status"),
                        "attempts": doc.get("attempts"),
                        "last_error": doc.get("last_error"),
                        "result": doc.get("result"),
                        "updated_at": doc.get("updated_at"),
                    },
                )
                return

            self._json(404, {"ok": False, "error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            route = parsed.path.rstrip("/")
            if route == "/v1/sync/batch":
                if not self._authorize():
                    return
                if not cfg.couchdb_url:
                    self._json(400, {"ok": False, "error": "couchdb_not_configured"})
                    return
                payload = self._read_json()
                docs = payload.get("docs")
                if not isinstance(docs, list):
                    self._json(400, {"ok": False, "error": "docs is required"})
                    return
                client = CouchClient(cfg.couchdb_url)
                client.ensure_db(cfg.couchdb_db_meta)
                written = 0
                for doc in docs:
                    if not isinstance(doc, dict):
                        continue
                    doc_id = str(doc.get("_id") or "")
                    if not doc_id:
                        continue
                    client.upsert_doc(cfg.couchdb_db_meta, doc_id, doc)
                    written += 1
                self._json(200, {"ok": True, "written": written})
                return

            if route not in {"/v1/captures", "/v1/import-url"}:
                self._json(404, {"ok": False, "error": "not_found"})
                return

            if not self._authorize():
                return

            payload = self._read_json()
            url = str(payload.get("url") or "").strip()
            if not url:
                self._json(400, {"ok": False, "error": "url is required"})
                return

            capture_id = str(payload.get("request_id") or uuid.uuid4())

            if cfg.couchdb_url:
                try:
                    client = CouchClient(cfg.couchdb_url)
                    client.ensure_db(cfg.couchdb_db_jobs)
                    job_doc = {
                        "_id": f"job:{capture_id}",
                        "type": "capture_job",
                        "status": "pending",
                        "attempts": 0,
                        "created_at": _utc_now_iso(),
                        "updated_at": _utc_now_iso(),
                        "payload": {
                            "url": url,
                            "title": payload.get("title"),
                            "context_text": payload.get("context_text") or payload.get("selection"),
                            "source_page": payload.get("source_page"),
                            "captured_at": payload.get("captured_at"),
                        },
                    }
                    client.upsert_doc(cfg.couchdb_db_jobs, f"job:{capture_id}", job_doc)
                    self._json(
                        202,
                        {
                            "ok": True,
                            "capture_id": capture_id,
                            "state": "queued",
                        },
                    )
                    return
                except Exception as exc:
                    self._json(500, {"ok": False, "error": f"failed_to_enqueue_capture: {exc}"})
                    return

            store = create_store(cfg)
            try:
                store.initialize()
                result = import_url_to_library(
                    store.db,
                    cfg.library_dir,
                    url,
                    page_title=payload.get("title"),
                    context_text=payload.get("context_text") or payload.get("selection"),
                )
                self._json(
                    200,
                    {
                        "ok": True,
                        "capture_id": capture_id,
                        "state": "imported",
                        "result": import_result_to_dict(result),
                    },
                )
            except Exception as exc:
                self._json(500, {"ok": False, "error": str(exc)})
            finally:
                store.close()

        def do_PATCH(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            route = parsed.path.rstrip("/")
            if not route.startswith("/v1/queue/"):
                self._json(404, {"ok": False, "error": "not_found"})
                return
            if not self._authorize():
                return
            paper_id = route.split("/")[-1]
            payload = self._read_json()
            status = str(payload.get("status") or "").strip()
            priority = payload.get("priority")
            if not status:
                self._json(400, {"ok": False, "error": "status is required"})
                return

            store = create_store(cfg)
            try:
                store.initialize()
                store.db.queue_set_status(paper_id, status, priority=float(priority) if priority is not None else None)
                self._json(200, {"ok": True, "paper_id": paper_id, "status": status, "priority": priority})
            except Exception as exc:
                self._json(500, {"ok": False, "error": str(exc)})
            finally:
                store.close()

    server = ThreadingHTTPServer((host, port), ApiHandler)
    print(f"PaperTool API listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
