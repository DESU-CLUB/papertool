from __future__ import annotations

from dataclasses import replace

from papertool.config import PaperToolConfig
from papertool.store.couch_store import CouchStore


class HybridStore(CouchStore):
    def __init__(self, config: PaperToolConfig) -> None:
        cfg = replace(config)
        cfg.storage_backend = "hybrid"
        super().__init__(cfg)
