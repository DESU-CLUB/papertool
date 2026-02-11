from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - py310 fallback
    import tomli as tomllib  # type: ignore


DEFAULT_CONFIG_PATH = Path("papertool.toml")


@dataclass(slots=True)
class PaperToolConfig:
    library_dir: Path
    db_path: Path
    obsidian_vault: Path | None = None
    obsidian_papers_dir: str = "Papers"
    obsidian_daily_dir: str = "Daily"
    retrieval_backend: str = "shadow"
    rust_index_dir: Path | None = None
    cluster_mode: str = "on_demand"
    storage_backend: str = "sqlite"
    remote_api_base_url: str | None = None
    remote_api_token: str | None = None
    couchdb_url: str | None = None
    couchdb_db_meta: str = "papertool_meta"
    couchdb_db_events: str = "papertool_events"
    couchdb_db_jobs: str = "papertool_jobs"
    minio_endpoint: str | None = None
    minio_bucket: str = "papertool-files"
    minio_access_key: str | None = None
    minio_secret_key: str | None = None
    sync_enabled: bool = True
    sync_pull_interval_sec: int = 30
    sync_push_interval_sec: int = 30


def _resolve_path(path_value: str | None, root: Path) -> Path | None:
    if not path_value:
        return None
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = (root / path).resolve()
    return path


def default_config(project_root: Path) -> PaperToolConfig:
    rust_index_dir = (project_root / ".papertool" / "index" / "v1").resolve()
    return PaperToolConfig(
        library_dir=(project_root / "library").resolve(),
        db_path=(project_root / ".papertool" / "papertool.db").resolve(),
        obsidian_vault=None,
        rust_index_dir=rust_index_dir,
    )


def load_config(config_path: Path | None = None) -> PaperToolConfig:
    path = config_path or DEFAULT_CONFIG_PATH
    path = path.expanduser().resolve()
    if not path.exists():
        project_root = path.parent
        return default_config(project_root)

    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    project_root = path.parent

    library_dir = _resolve_path(raw.get("library_dir"), project_root)
    db_path = _resolve_path(raw.get("db_path"), project_root)
    obsidian_vault = _resolve_path(raw.get("obsidian_vault"), project_root)
    rust_index_dir = _resolve_path(raw.get("rust_index_dir"), project_root)

    cfg = default_config(project_root)
    return PaperToolConfig(
        library_dir=library_dir or cfg.library_dir,
        db_path=db_path or cfg.db_path,
        obsidian_vault=obsidian_vault,
        obsidian_papers_dir=str(raw.get("obsidian_papers_dir") or cfg.obsidian_papers_dir),
        obsidian_daily_dir=str(raw.get("obsidian_daily_dir") or cfg.obsidian_daily_dir),
        retrieval_backend=str(raw.get("retrieval_backend") or cfg.retrieval_backend),
        rust_index_dir=rust_index_dir or cfg.rust_index_dir,
        cluster_mode=str(raw.get("cluster_mode") or cfg.cluster_mode),
        storage_backend=str(raw.get("storage_backend") or cfg.storage_backend),
        remote_api_base_url=(str(raw.get("remote_api_base_url")) if raw.get("remote_api_base_url") else None),
        remote_api_token=(str(raw.get("remote_api_token")) if raw.get("remote_api_token") else None),
        couchdb_url=(str(raw.get("couchdb_url")) if raw.get("couchdb_url") else None),
        couchdb_db_meta=str(raw.get("couchdb_db_meta") or cfg.couchdb_db_meta),
        couchdb_db_events=str(raw.get("couchdb_db_events") or cfg.couchdb_db_events),
        couchdb_db_jobs=str(raw.get("couchdb_db_jobs") or cfg.couchdb_db_jobs),
        minio_endpoint=(str(raw.get("minio_endpoint")) if raw.get("minio_endpoint") else None),
        minio_bucket=str(raw.get("minio_bucket") or cfg.minio_bucket),
        minio_access_key=(str(raw.get("minio_access_key")) if raw.get("minio_access_key") else None),
        minio_secret_key=(str(raw.get("minio_secret_key")) if raw.get("minio_secret_key") else None),
        sync_enabled=bool(raw.get("sync_enabled", cfg.sync_enabled)),
        sync_pull_interval_sec=int(raw.get("sync_pull_interval_sec") or cfg.sync_pull_interval_sec),
        sync_push_interval_sec=int(raw.get("sync_push_interval_sec") or cfg.sync_push_interval_sec),
    )


def dump_config(config: PaperToolConfig, config_path: Path | None = None) -> Path:
    path = (config_path or DEFAULT_CONFIG_PATH).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    def _display(path_value: Path | None) -> str:
        return str(path_value) if path_value else ""

    lines = [
        f'library_dir = "{_display(config.library_dir)}"',
        f'db_path = "{_display(config.db_path)}"',
        f'obsidian_vault = "{_display(config.obsidian_vault)}"',
        f'obsidian_papers_dir = "{config.obsidian_papers_dir}"',
        f'obsidian_daily_dir = "{config.obsidian_daily_dir}"',
        f'retrieval_backend = "{config.retrieval_backend}"',
        f'rust_index_dir = "{_display(config.rust_index_dir)}"',
        f'cluster_mode = "{config.cluster_mode}"',
        f'storage_backend = "{config.storage_backend}"',
        f'remote_api_base_url = "{config.remote_api_base_url or ""}"',
        f'remote_api_token = "{config.remote_api_token or ""}"',
        f'couchdb_url = "{config.couchdb_url or ""}"',
        f'couchdb_db_meta = "{config.couchdb_db_meta}"',
        f'couchdb_db_events = "{config.couchdb_db_events}"',
        f'couchdb_db_jobs = "{config.couchdb_db_jobs}"',
        f'minio_endpoint = "{config.minio_endpoint or ""}"',
        f'minio_bucket = "{config.minio_bucket}"',
        f'minio_access_key = "{config.minio_access_key or ""}"',
        f'minio_secret_key = "{config.minio_secret_key or ""}"',
        f"sync_enabled = {'true' if config.sync_enabled else 'false'}",
        f"sync_pull_interval_sec = {int(config.sync_pull_interval_sec)}",
        f"sync_push_interval_sec = {int(config.sync_push_interval_sec)}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def config_from_kwargs(project_root: Path, values: dict[str, Any]) -> PaperToolConfig:
    cfg = default_config(project_root)
    library_dir = values.get("library_dir")
    db_path = values.get("db_path")
    obsidian_vault = values.get("obsidian_vault")
    rust_index_dir = values.get("rust_index_dir")

    if library_dir:
        cfg.library_dir = Path(library_dir).expanduser().resolve()
    if db_path:
        cfg.db_path = Path(db_path).expanduser().resolve()
    if obsidian_vault:
        cfg.obsidian_vault = Path(obsidian_vault).expanduser().resolve()
    if rust_index_dir:
        cfg.rust_index_dir = Path(rust_index_dir).expanduser().resolve()

    cfg.obsidian_papers_dir = values.get("obsidian_papers_dir") or cfg.obsidian_papers_dir
    cfg.obsidian_daily_dir = values.get("obsidian_daily_dir") or cfg.obsidian_daily_dir
    cfg.retrieval_backend = str(values.get("retrieval_backend") or cfg.retrieval_backend)
    cfg.cluster_mode = str(values.get("cluster_mode") or cfg.cluster_mode)
    cfg.storage_backend = str(values.get("storage_backend") or cfg.storage_backend)
    cfg.remote_api_base_url = values.get("remote_api_base_url") or cfg.remote_api_base_url
    cfg.remote_api_token = values.get("remote_api_token") or cfg.remote_api_token
    cfg.couchdb_url = values.get("couchdb_url") or cfg.couchdb_url
    cfg.couchdb_db_meta = values.get("couchdb_db_meta") or cfg.couchdb_db_meta
    cfg.couchdb_db_events = values.get("couchdb_db_events") or cfg.couchdb_db_events
    cfg.couchdb_db_jobs = values.get("couchdb_db_jobs") or cfg.couchdb_db_jobs
    cfg.minio_endpoint = values.get("minio_endpoint") or cfg.minio_endpoint
    cfg.minio_bucket = values.get("minio_bucket") or cfg.minio_bucket
    cfg.minio_access_key = values.get("minio_access_key") or cfg.minio_access_key
    cfg.minio_secret_key = values.get("minio_secret_key") or cfg.minio_secret_key
    cfg.sync_enabled = bool(values.get("sync_enabled", cfg.sync_enabled))
    cfg.sync_pull_interval_sec = int(values.get("sync_pull_interval_sec") or cfg.sync_pull_interval_sec)
    cfg.sync_push_interval_sec = int(values.get("sync_push_interval_sec") or cfg.sync_push_interval_sec)
    return cfg
