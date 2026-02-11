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


def _resolve_path(path_value: str | None, root: Path) -> Path | None:
    if not path_value:
        return None
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = (root / path).resolve()
    return path


def default_config(project_root: Path) -> PaperToolConfig:
    return PaperToolConfig(
        library_dir=(project_root / "library").resolve(),
        db_path=(project_root / ".papertool" / "papertool.db").resolve(),
        obsidian_vault=None,
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

    cfg = default_config(project_root)
    return PaperToolConfig(
        library_dir=library_dir or cfg.library_dir,
        db_path=db_path or cfg.db_path,
        obsidian_vault=obsidian_vault,
        obsidian_papers_dir=str(raw.get("obsidian_papers_dir") or cfg.obsidian_papers_dir),
        obsidian_daily_dir=str(raw.get("obsidian_daily_dir") or cfg.obsidian_daily_dir),
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
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def config_from_kwargs(project_root: Path, values: dict[str, Any]) -> PaperToolConfig:
    cfg = default_config(project_root)
    library_dir = values.get("library_dir")
    db_path = values.get("db_path")
    obsidian_vault = values.get("obsidian_vault")

    if library_dir:
        cfg.library_dir = Path(library_dir).expanduser().resolve()
    if db_path:
        cfg.db_path = Path(db_path).expanduser().resolve()
    if obsidian_vault:
        cfg.obsidian_vault = Path(obsidian_vault).expanduser().resolve()

    cfg.obsidian_papers_dir = values.get("obsidian_papers_dir") or cfg.obsidian_papers_dir
    cfg.obsidian_daily_dir = values.get("obsidian_daily_dir") or cfg.obsidian_daily_dir
    return cfg
