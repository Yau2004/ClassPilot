import argparse
import sys

from sqlalchemy import text

from app.core.config import Settings
from app.db.session import create_database_engine


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a read-only SELECT 1 check against one configured database."
    )
    parser.add_argument("target", choices=("dev", "test"))
    target = parser.parse_args().target

    try:
        settings = Settings()
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
                result, actual_name = connection.execute(
                    text("SELECT 1, current_database()")
                ).one()
            if actual_name != expected_name:
                raise RuntimeError("Connected database name did not match the target.")
            print(f"Read-only database check succeeded: target={target}, database={actual_name}, result={result}")
            return 0
        finally:
            engine.dispose()
    except Exception as exc:
        print(
            f"Database check failed for target={target}: {type(exc).__name__}. "
            "Details are omitted to avoid exposing credentials.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
