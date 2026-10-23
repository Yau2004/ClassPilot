from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

from app.core.runtime import RUNTIME_PATHS

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = PROJECT_ROOT / ".env"
TEST_DATABASE_NAME = "steam_classroom_test"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    postgres_host: str = "localhost"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_user: str = "steam_app"
    postgres_password: SecretStr = SecretStr("")
    postgres_db: str = "steam_classroom_dev"
    postgres_test_db: str | None = None

    def database_url(self, target: Literal["dev", "test"]) -> URL:
        if target == "dev":
            database_name = self.postgres_db
        else:
            if not self.postgres_test_db:
                raise ValueError(
                    "POSTGRES_TEST_DB is required for test database access; "
                    "there is no development database fallback."
                )
            if self.postgres_test_db != TEST_DATABASE_NAME:
                raise ValueError(
                    f"Test database must be named {TEST_DATABASE_NAME}."
                )
            database_name = self.postgres_test_db

        password = self.postgres_password.get_secret_value()
        if not password:
            raise ValueError(
                f"POSTGRES_PASSWORD is empty. Set it in the local file: {ENV_FILE}"
            )

        return URL.create(
            drivername="postgresql+psycopg",
            username=self.postgres_user,
            password=password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=database_name,
        )


class ReleaseSettings(BaseModel):
    """Validated release values with no environment or dotenv source."""

    postgres_host: str = "localhost"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_user: str = "steam_app"
    postgres_password: SecretStr = SecretStr("")
    postgres_db: str = "classpilot_release"
    postgres_test_db: str | None = None

    def database_url(self, target: Literal["dev", "test"] = "dev") -> URL:
        if target == "test":
            raise ValueError("A release runtime has no test database target.")
        password = self.postgres_password.get_secret_value()
        if not password:
            raise ValueError("Release database credentials are unavailable.")
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.postgres_user,
            password=password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


class ReleaseSettingsUnavailable(ValueError):
    """Raised until the release configuration provider is implemented."""


_release_settings: ReleaseSettings | None = None


def configure_release_settings(settings: ReleaseSettings | None) -> None:
    """Install process-local release settings; never reads dotenv or env vars."""
    global _release_settings
    _release_settings = settings


def get_settings() -> Settings | ReleaseSettings:
    if RUNTIME_PATHS.frozen:
        if _release_settings is None:
            raise ReleaseSettingsUnavailable("Release settings are not configured.")
        return _release_settings
    return Settings()
