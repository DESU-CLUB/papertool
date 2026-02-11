from __future__ import annotations

from typing import Any

from papertool.config import PaperToolConfig
from papertool.db import PaperDB


class SQLiteStore:
    def __init__(self, config: PaperToolConfig) -> None:
        self.config = config
        self.db = PaperDB(config.db_path)

    def initialize(self) -> None:
        self.db.initialize()

    def close(self) -> None:
        self.db.close()

    def sync_run(self, *, pull: bool = True, push: bool = True) -> dict[str, Any]:
        return {
            "ok": True,
            "backend": "sqlite",
            "pull": False,
            "push": False,
            "message": "sync disabled for sqlite backend",
        }

    def sync_status(self) -> dict[str, Any]:
        return {
            "backend": "sqlite",
            "sync_enabled": False,
            "state": [],
        }

    def remote_health(self) -> dict[str, Any]:
        return {
            "ok": False,
            "backend": "sqlite",
            "error": "remote backend is not configured",
        }
