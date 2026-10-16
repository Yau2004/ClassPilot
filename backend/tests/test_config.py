import pytest
from pydantic import SecretStr

from app.core.config import ENV_FILE, Settings


def test_test_database_url_requires_explicit_test_database() -> None:
    settings = Settings(
        postgres_password=SecretStr("test-placeholder"), postgres_test_db=None
    )

    with pytest.raises(ValueError, match="no development database fallback"):
        settings.database_url("test")


def test_test_database_url_targets_only_test_database() -> None:
    settings = Settings(
        postgres_password=SecretStr("test-placeholder"),
        postgres_test_db="steam_classroom_test",
    )

    url = settings.database_url("test")

    assert url.drivername == "postgresql+psycopg"
    assert url.database == "steam_classroom_test"
    assert url.password == "test-placeholder"


def test_empty_password_message_does_not_include_password_value() -> None:
    settings = Settings(postgres_password=SecretStr(""))

    with pytest.raises(ValueError, match="POSTGRES_PASSWORD is empty") as exc_info:
        settings.database_url("dev")

    assert str(ENV_FILE) in str(exc_info.value)
