from alembic import context
from sqlalchemy import create_engine, pool, text
from sqlmodel import SQLModel

from app.core.config import TEST_DATABASE_NAME, get_settings
from app.core.runtime import RUNTIME_PATHS
from app.models import (  # noqa: F401 - registers SQLModel metadata
    AttendanceRecord,
    Classroom,
    LessonSession,
    Student,
    StudentClassEnrollment,
    SessionParticipant,
)

config = context.config
target_metadata = SQLModel.metadata


def get_database_target() -> str:
    target = context.get_x_argument(as_dictionary=True).get("database")
    if target not in {"dev", "test", "release"}:
        raise ValueError(
            "Choose an explicit migration target with -x database=dev, test, or release."
        )
    return target


def run_migrations_offline() -> None:
    url = get_settings().database_url(get_database_target())
    context.configure(
        url=url.render_as_string(hide_password=True),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    target = get_database_target()
    if target == "release" and not RUNTIME_PATHS.frozen:
        raise ValueError("The release migration target is available only in a frozen runtime.")
    settings = get_settings()
    if target == "dev":
        expected_database = settings.postgres_db
    elif target == "test":
        expected_database = TEST_DATABASE_NAME
    else:
        expected_database = settings.postgres_db
    engine = create_engine(
        settings.database_url(target), poolclass=pool.NullPool
    )
    with engine.connect() as connection:
        actual_database = connection.execute(
            text("SELECT current_database()")
        ).scalar_one()
        if actual_database != expected_database:
            raise RuntimeError(
                f"Refusing migration: expected database {expected_database}, "
                f"connected to {actual_database}."
            )
        print(
            f"Verified migration target: target={target} "
            f"database={actual_database}",
            flush=True,
        )
        # End the implicit transaction opened by the read-only preflight query.
        # Alembic must own the transaction that applies the migration.
        connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
