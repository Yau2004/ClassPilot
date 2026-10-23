"""Resolve development and frozen-release resource and state locations."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class RuntimePaths:
    frozen: bool
    resource_root: Path
    project_root: Path
    backend_root: Path
    frontend_dist: Path
    alembic_ini: Path
    alembic_root: Path
    local_app_data: Path
    config_dir: Path
    settings_file: Path
    log_dir: Path
    postgres_data_dir: Path
    backup_dir: Path


def resolve_runtime_paths(
    *,
    frozen: bool | None = None,
    resource_root: Path | None = None,
    local_app_data: Path | None = None,
    state_root: Path | None = None,
) -> RuntimePaths:
    """Return paths without creating directories or reading configuration."""
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if is_frozen:
        bundled_root = resource_root or Path(getattr(sys, "_MEIPASS"))
        project_root = bundled_root
    else:
        project_root = resource_root or PROJECT_ROOT
        bundled_root = project_root

    app_data = local_app_data or Path(
        os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
    )
    resolved_state_root = state_root or app_data / "ClassPilot"
    return RuntimePaths(
        frozen=is_frozen,
        resource_root=bundled_root,
        project_root=project_root,
        backend_root=bundled_root / "backend",
        frontend_dist=bundled_root / "frontend" / "dist",
        alembic_ini=bundled_root / "backend" / "alembic.ini",
        alembic_root=bundled_root / "backend" / "alembic",
        local_app_data=app_data,
        config_dir=resolved_state_root / "config",
        settings_file=resolved_state_root / "config" / "settings.dpapi",
        log_dir=resolved_state_root / "logs",
        postgres_data_dir=resolved_state_root / "data" / "postgresql",
        backup_dir=resolved_state_root / "backups",
    )


RUNTIME_PATHS = resolve_runtime_paths()


def configure_release_data_root(state_root: Path) -> RuntimePaths:
    """Point writable release paths at a previously selected user data root."""
    global RUNTIME_PATHS
    if not RUNTIME_PATHS.frozen:
        raise RuntimeError("Release data locations can only be configured in frozen mode.")
    RUNTIME_PATHS = resolve_runtime_paths(
        frozen=True,
        resource_root=RUNTIME_PATHS.resource_root,
        local_app_data=RUNTIME_PATHS.local_app_data,
        state_root=state_root,
    )
    return RUNTIME_PATHS
