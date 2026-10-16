from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

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


def get_settings() -> Settings:
    return Settings()
