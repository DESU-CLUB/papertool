from __future__ import annotations

from papertool.config import PaperToolConfig
from papertool.store.base import Store
from papertool.store.couch_store import CouchStore
from papertool.store.hybrid_store import HybridStore
from papertool.store.sqlite_store import SQLiteStore


def create_store(config: PaperToolConfig) -> Store:
    backend = (config.storage_backend or "sqlite").strip().lower()
    if backend == "sqlite":
        return SQLiteStore(config)
    if backend == "hybrid":
        return HybridStore(config)
    if backend == "couch":
        return CouchStore(config)
    return SQLiteStore(config)
