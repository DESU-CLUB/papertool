from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from papertool.config import load_config
from papertool.db import PaperDB
from papertool.url_import import import_result_to_dict, import_url_to_library


def run_bridge_server(host: str = "127.0.0.1", port: int = 17345) -> None:
    cfg = load_config()
    cfg.library_dir.mkdir(parents=True, exist_ok=True)

    class BridgeHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def _json(self, status: int, payload: dict[str, object]) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode("utf-8"))

        def do_OPTIONS(self) -> None:  # noqa: N802
            self._json(204, {})

        def do_GET(self) -> None:  # noqa: N802
            if self.path.rstrip("/") == "/health":
                self._json(
                    200,
                    {
                        "ok": True,
                        "library_dir": str(cfg.library_dir),
                        "db_path": str(cfg.db_path),
                    },
                )
                return
            self._json(404, {"ok": False, "error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            route = self.path.rstrip("/")
            if route not in {"/capture", "/import"}:
                self._json(404, {"ok": False, "error": "not_found"})
                return

            content_length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(content_length) if content_length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                self._json(400, {"ok": False, "error": "invalid_json"})
                return

            url = str(payload.get("url") or "").strip()
            if not url:
                self._json(400, {"ok": False, "error": "url is required"})
                return

            db: PaperDB | None = None
            try:
                db = PaperDB(cfg.db_path)
                db.initialize()
                result = import_url_to_library(
                    db,
                    cfg.library_dir,
                    url,
                    page_title=payload.get("title"),
                    context_text=payload.get("context_text") or payload.get("selection"),
                )
                db.close()
            except Exception as exc:
                try:
                    if db is not None:
                        db.close()
                except Exception:
                    pass
                self._json(500, {"ok": False, "error": str(exc)})
                return

            self._json(200, {"ok": True, "result": import_result_to_dict(result)})

    server = ThreadingHTTPServer((host, port), BridgeHandler)
    print(f"PaperTool bridge listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
