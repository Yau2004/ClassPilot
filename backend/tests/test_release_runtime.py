from pathlib import Path

import pytest

from app.core import config
from app.core.release_settings import validate_release_settings
from app.core.runtime import resolve_runtime_paths


def test_development_paths_remain_repository_relative() -> None:
    root = Path(__file__).resolve().parents[2]

    paths = resolve_runtime_paths(frozen=False)

    assert paths.project_root == root
    assert paths.frontend_dist == root / "frontend" / "dist"
    assert paths.alembic_ini == root / "backend" / "alembic.ini"
    assert config.ENV_FILE == root / ".env"


def test_release_paths_are_injected_and_do_not_create_local_data(tmp_path: Path) -> None:
    resources = tmp_path / "resources with 中文"
    local = tmp_path / "local app data"
    paths = resolve_runtime_paths(
        frozen=True, resource_root=resources, local_app_data=local
    )

    assert paths.resource_root == resources
    assert paths.frontend_dist == resources / "frontend" / "dist"
    assert paths.alembic_ini == resources / "backend" / "alembic.ini"
    assert paths.settings_file == local / "ClassPilot" / "config" / "settings.dpapi"
    assert paths.log_dir == local / "ClassPilot" / "logs"
    assert paths.postgres_data_dir == local / "ClassPilot" / "data" / "postgresql"
    assert paths.backup_dir == local / "ClassPilot" / "backups"
    assert not local.exists()


def test_release_settings_never_fall_back_to_development_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dev_dotenv = tmp_path / ".env"
    dev_dotenv.write_text("POSTGRES_PASSWORD=prototype-sentinel\n", encoding="utf-8")
    monkeypatch.setattr(
        config,
        "RUNTIME_PATHS",
        resolve_runtime_paths(
            frozen=True,
            resource_root=tmp_path / "resources",
            local_app_data=tmp_path / "local",
        ),
    )
    monkeypatch.setattr(config, "ENV_FILE", dev_dotenv)

    with pytest.raises(config.ReleaseSettingsUnavailable):
        config.get_settings()

    assert dev_dotenv.read_text(encoding="utf-8") == "POSTGRES_PASSWORD=prototype-sentinel\n"
    assert not (tmp_path / "local").exists()


def test_release_settings_validation_uses_explicit_values_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POSTGRES_DB", "environment-value-must-not-win")

    settings = validate_release_settings({"postgres_db": "explicit-release-value"})

    assert settings.postgres_db == "explicit-release-value"
    assert settings.postgres_password.get_secret_value() == ""
