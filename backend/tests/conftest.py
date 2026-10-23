from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from app.core.config import TEST_DATABASE_NAME, get_settings
from app.db.diagnostics import redact_diagnostic_text, safe_database_error_summary
from app.db.session import create_database_engine, get_session
from app.main import app

_NO_OVERRIDE = object()
EXPECTED_TEST_SCHEMA_REVISION = "20261019_0004"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    try:
        password = get_settings().postgres_password.get_secret_value()
    except Exception:
        password = ""
    if not password:
        return

    for index, (section_name, captured) in enumerate(report.sections):
        if captured:
            report.sections[index] = (
                section_name,
                redact_diagnostic_text(captured, [password]),
            )
    if report.longrepr is not None:
        report.longrepr = redact_diagnostic_text(str(report.longrepr), [password])


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    settings = get_settings()
    if settings.postgres_test_db != TEST_DATABASE_NAME:
        raise RuntimeError(
            "API tests require POSTGRES_TEST_DB=steam_classroom_test."
        )

    engine = create_database_engine("test", settings)
    previous_override = app.dependency_overrides.get(get_session, _NO_OVERRIDE)

    try:
        with engine.connect() as connection:
            outer_transaction = connection.begin()
            verified_database = connection.execute(
                text("SELECT current_database()")
            ).scalar_one()
            if verified_database != TEST_DATABASE_NAME:
                outer_transaction.rollback()
                raise RuntimeError(
                    "Refusing API tests because the connected database is not "
                    "steam_classroom_test."
                )

            version_table = connection.execute(
                text("SELECT to_regclass('public.alembic_version')")
            ).scalar_one()
            revision = (
                connection.execute(
                    text("SELECT version_num FROM public.alembic_version")
                ).scalar_one_or_none()
                if version_table is not None
                else None
            )
            if revision != EXPECTED_TEST_SCHEMA_REVISION:
                outer_transaction.rollback()
                raise RuntimeError(
                    "API tests require the test database migration at "
                    f"revision {EXPECTED_TEST_SCHEMA_REVISION}; found {revision!r}."
                )

            def override_get_session() -> Generator[Session, None, None]:
                with Session(
                    bind=connection,
                    join_transaction_mode="create_savepoint",
                ) as session:
                    yield session

            app.dependency_overrides[get_session] = override_get_session
            try:
                with TestClient(app) as test_client:
                    yield test_client
            finally:
                if previous_override is _NO_OVERRIDE:
                    app.dependency_overrides.pop(get_session, None)
                else:
                    app.dependency_overrides[get_session] = previous_override

                if outer_transaction.is_active:
                    cleanup_database = connection.execute(
                        text("SELECT current_database()")
                    ).scalar_one()
                    if cleanup_database != TEST_DATABASE_NAME:
                        outer_transaction.rollback()
                        raise RuntimeError(
                            "Refusing test transaction cleanup because the "
                            "database is not steam_classroom_test."
                        )
                    outer_transaction.rollback()
    except SQLAlchemyError as error:
        raise RuntimeError(safe_database_error_summary(error)) from None
    finally:
        engine.dispose()
