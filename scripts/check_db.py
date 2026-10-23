import argparse
from pathlib import Path
import sys

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import Settings
from app.db.session import create_database_engine

REQUIRED_SCHEMA_REVISION = "20261019_0004"
PASSWORD_PLACEHOLDER = "CHANGE_ME_WITH_YOUR_LOCAL_PASSWORD"


def schema_status(actual_revision: str | None) -> tuple[bool, str]:
    if actual_revision == REQUIRED_SCHEMA_REVISION:
        return True, f"schema revision={actual_revision}"
    shown_revision = actual_revision or "missing"
    return (
        False,
        f"schema revision mismatch: current={shown_revision}, "
        f"required={REQUIRED_SCHEMA_REVISION}",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a read-only database and schema readiness check."
    )
    parser.add_argument("target", choices=("dev", "test"))
    target = parser.parse_args().target

    try:
        settings = Settings()
        password = settings.postgres_password.get_secret_value()
        if not password or password == PASSWORD_PLACEHOLDER:
            print(
                "POSTGRES_PASSWORD is not configured. Set it in the project-root .env.",
                file=sys.stderr,
            )
            return 3
        expected_name = (
            settings.postgres_db
            if target == "dev"
            else settings.postgres_test_db
        )
        if target == "test" and expected_name != "steam_classroom_test":
            raise ValueError(
                "POSTGRES_TEST_DB must explicitly be steam_classroom_test."
            )

        engine = create_database_engine(target, settings)
        try:
            with engine.connect() as connection:
                actual_name = connection.execute(
                    text("SELECT current_database()")
                ).scalar_one()
            if actual_name != expected_name:
                print(
                    f"Database target mismatch: target={target}, "
                    f"expected_database={expected_name}, actual_database={actual_name}",
                    file=sys.stderr,
                )
                return 1

            with engine.connect() as connection:
                version_table = connection.execute(
                    text("SELECT to_regclass('public.alembic_version')")
                ).scalar_one()
                actual_revision = (
                    connection.execute(
                        text("SELECT version_num FROM public.alembic_version")
                    ).scalar_one_or_none()
                    if version_table is not None
                    else None
                )
            revision_matches, revision_message = schema_status(actual_revision)
            if not revision_matches:
                print(
                    f"Read-only database check connected: target={target}, "
                    f"database={actual_name}; {revision_message}. "
                    "No migration was run.",
                    file=sys.stderr,
                )
                return 2
            print(
                f"Read-only database check succeeded: target={target}, "
                f"database={actual_name}, {revision_message}"
            )
            return 0
        finally:
            engine.dispose()
    except SQLAlchemyError as exc:
        print(
            f"Database connection or read-only check failed for target={target} "
            f"({type(exc).__name__}). Confirm PostgreSQL is running and the "
            "local host, port, role and database settings are correct. "
            "Connection details are omitted.",
            file=sys.stderr,
        )
        return 1
    except ValueError:
        print(
            f"Database settings are incomplete or invalid for target={target}. "
            "Review the local .env fields; configured values are omitted.",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:
        print(
            f"Database check failed for target={target}: {type(exc).__name__}. "
            "Details are omitted to avoid exposing credentials.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
