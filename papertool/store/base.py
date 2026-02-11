from __future__ import annotations

from typing import Any, Protocol

from papertool.config import PaperToolConfig
from papertool.db import PaperDB


class Store(Protocol):
    config: PaperToolConfig
    db: PaperDB

    def initialize(self) -> None: ...

    def close(self) -> None: ...

    def sync_run(self, *, pull: bool = True, push: bool = True) -> dict[str, Any]: ...

    def sync_status(self) -> dict[str, Any]: ...

    def remote_health(self) -> dict[str, Any]: ...
