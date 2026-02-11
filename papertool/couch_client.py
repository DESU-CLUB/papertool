from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


@dataclass(slots=True)
class CouchResponse:
    status: int
    payload: dict[str, Any]


class CouchClient:
    def __init__(self, base_url: str, timeout: int = 20) -> None:
        if not base_url:
            raise ValueError("couchdb_url is required")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        parsed = urlparse(self.base_url)
        self._auth_header: str | None = None
        if parsed.username:
            user = parsed.username
            pwd = parsed.password or ""
            token = base64.b64encode(f"{user}:{pwd}".encode("utf-8")).decode("ascii")
            self._auth_header = f"Basic {token}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | list[dict[str, Any]] | None = None,
        expected: set[int] | None = None,
    ) -> CouchResponse:
        url = f"{self.base_url}{path}"
        headers = {"Content-Type": "application/json"}
        if self._auth_header:
            headers["Authorization"] = self._auth_header
        payload = None if body is None else json.dumps(body).encode("utf-8")
        req = Request(url, data=payload, headers=headers, method=method)
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                data = json.loads(raw.decode("utf-8")) if raw else {}
                if expected and resp.status not in expected:
                    raise RuntimeError(f"Unexpected status {resp.status}: {data}")
                return CouchResponse(status=resp.status, payload=data)
        except HTTPError as exc:
            raw = exc.read()
            text = raw.decode("utf-8", errors="ignore")
            try:
                payload_obj = json.loads(text) if text else {}
            except Exception:
                payload_obj = {"error": text or str(exc)}
            if expected and exc.code in expected:
                return CouchResponse(status=exc.code, payload=payload_obj)
            raise RuntimeError(f"CouchDB HTTP {exc.code}: {payload_obj}") from exc
        except URLError as exc:
            raise RuntimeError(f"CouchDB connection failed: {exc}") from exc

    def health(self) -> dict[str, Any]:
        resp = self._request("GET", "/", expected={200})
        return resp.payload

    def ensure_db(self, db_name: str) -> None:
        encoded = quote(db_name, safe="")
        resp = self._request("PUT", f"/{encoded}", expected={201, 202, 412})
        if resp.status not in {201, 202, 412}:
            raise RuntimeError(f"Failed to ensure db {db_name}: {resp.payload}")

    def get_doc(self, db_name: str, doc_id: str) -> dict[str, Any] | None:
        encoded_db = quote(db_name, safe="")
        encoded_id = quote(doc_id, safe="")
        resp = self._request("GET", f"/{encoded_db}/{encoded_id}", expected={200, 404})
        if resp.status == 404:
            return None
        return resp.payload

    def upsert_doc(self, db_name: str, doc_id: str, doc: dict[str, Any]) -> dict[str, Any]:
        encoded_db = quote(db_name, safe="")
        encoded_id = quote(doc_id, safe="")
        current = self.get_doc(db_name, doc_id)
        payload = dict(doc)
        payload["_id"] = doc_id
        if current and current.get("_rev"):
            payload["_rev"] = current["_rev"]
        resp = self._request("PUT", f"/{encoded_db}/{encoded_id}", body=payload, expected={201, 202})
        return resp.payload

    def bulk_docs(self, db_name: str, docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        encoded_db = quote(db_name, safe="")
        resp = self._request("POST", f"/{encoded_db}/_bulk_docs", body={"docs": docs}, expected={201, 202})
        out = resp.payload
        if isinstance(out, list):
            return out
        return []

    def all_docs(self, db_name: str, *, include_docs: bool = True, limit: int = 100_000) -> list[dict[str, Any]]:
        encoded_db = quote(db_name, safe="")
        path = f"/{encoded_db}/_all_docs?include_docs={'true' if include_docs else 'false'}&limit={int(limit)}"
        resp = self._request("GET", path, expected={200})
        rows = resp.payload.get("rows") or []
        if not isinstance(rows, list):
            return []
        return [dict(row) for row in rows if isinstance(row, dict)]

    def changes(
        self,
        db_name: str,
        *,
        since: str = "0",
        include_docs: bool = True,
        limit: int = 1000,
    ) -> dict[str, Any]:
        encoded_db = quote(db_name, safe="")
        path = (
            f"/{encoded_db}/_changes?since={quote(since, safe='')}&include_docs="
            f"{'true' if include_docs else 'false'}&limit={int(limit)}"
        )
        resp = self._request("GET", path, expected={200})
        return resp.payload
