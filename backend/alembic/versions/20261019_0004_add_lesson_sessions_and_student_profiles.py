"""Add student profile fields and lesson sessions without converting history.

Revision ID: 20261019_0004
Revises: 20261015_0003
Create Date: 2026-10-19
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

revision: str = "20261019_0004"
down_revision: str | None = "20261015_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("students", "age", existing_type=sa.Integer(), nullable=True)
    op.add_column("students", sa.Column("gender", sa.String(length=24), nullable=True))
    op.add_column(
        "students",
        sa.Column(
            "support_need_status",
            sa.String(length=20),
            server_default="not_provided",
            nullable=False,
        ),
    )
    op.add_column(
        "students",
        sa.Column(
            "support_need_tags",
            postgresql.ARRAY(sa.String()),
            server_default=sa.text("ARRAY[]::VARCHAR[]"),
            nullable=False,
        ),
    )
    op.add_column("students", sa.Column("support_notes", sa.Text(), nullable=True))
    op.add_column("students", sa.Column("general_notes", sa.Text(), nullable=True))
    op.add_column(
        "students",
        sa.Column("is_archived", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.create_check_constraint(
        "ck_students_gender",
        "students",
        "gender IS NULL OR gender IN ('female', 'male', 'other', 'prefer_not_to_say')",
    )
    op.create_check_constraint(
        "ck_students_support_need_status",
        "students",
        "support_need_status IN ('not_provided', 'none_reported', 'needs_support')",
    )

    op.create_table(
        "lesson_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("course_type", sa.String(length=100), nullable=True),
        sa.Column("topic", sa.Text(), nullable=True),
        sa.Column("session_date", sa.Date(), nullable=True),
        sa.Column("start_time", sa.Time(timezone=False), nullable=True),
        sa.Column("end_time", sa.Time(timezone=False), nullable=True),
        sa.Column("confirmation_status", sa.String(length=12), server_default="pending", nullable=False),
        sa.Column("lifecycle_status", sa.String(length=12), server_default="planned", nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "(session_date IS NOT NULL OR (start_time IS NULL AND end_time IS NULL))",
            name="ck_lesson_sessions_times_require_date",
        ),
        sa.CheckConstraint(
            "(start_time IS NULL AND end_time IS NULL) OR "
            "(start_time IS NOT NULL AND end_time IS NOT NULL AND start_time < end_time)",
            name="ck_lesson_sessions_time_pair_and_order",
        ),
        sa.CheckConstraint(
            "confirmation_status IN ('pending', 'confirmed')",
            name="ck_lesson_sessions_confirmation_status",
        ),
        sa.CheckConstraint(
            "lifecycle_status IN ('planned', 'cancelled', 'completed')",
            name="ck_lesson_sessions_lifecycle_status",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_lesson_sessions"),
    )
    op.create_index(
        "ix_lesson_sessions_date_status",
        "lesson_sessions",
        ["session_date", "lifecycle_status"],
        unique=False,
    )
    op.create_table(
        "session_participants",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("attendance_status", sa.String(length=12), server_default="unrecorded", nullable=False),
        sa.Column("evaluation", sa.Text(), nullable=True),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "attendance_status IN ('unrecorded', 'present', 'absent', 'late', 'excused')",
            name="ck_session_participants_attendance_status",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["lesson_sessions.id"],
            name="session_participants_session_id_fkey", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["student_id"], ["students.id"],
            name="session_participants_student_id_fkey", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_session_participants"),
        sa.UniqueConstraint(
            "session_id", "student_id",
            name="uq_session_participants_session_student",
        ),
    )
    op.create_index(
        "ix_session_participants_student",
        "session_participants",
        ["student_id", "session_id"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    has_session_history = bind.execute(
        text("SELECT EXISTS (SELECT 1 FROM lesson_sessions) OR EXISTS "
             "(SELECT 1 FROM session_participants)")
    ).scalar_one()
    has_profile_data = bind.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM students WHERE age IS NULL "
            "OR gender IS NOT NULL OR support_need_status <> 'not_provided' "
            "OR cardinality(support_need_tags) > 0 OR support_notes IS NOT NULL "
            "OR general_notes IS NOT NULL OR is_archived)"
        )
    ).scalar_one()
    if has_session_history or has_profile_data:
        raise RuntimeError(
            "Refusing lossy downgrade: lesson, participant, or student profile data exists."
        )

    op.drop_index("ix_session_participants_student", table_name="session_participants")
    op.drop_table("session_participants")
    op.drop_index("ix_lesson_sessions_date_status", table_name="lesson_sessions")
    op.drop_table("lesson_sessions")
    op.drop_constraint("ck_students_support_need_status", "students", type_="check")
    op.drop_constraint("ck_students_gender", "students", type_="check")
    op.drop_column("students", "is_archived")
    op.drop_column("students", "general_notes")
    op.drop_column("students", "support_notes")
    op.drop_column("students", "support_need_tags")
    op.drop_column("students", "support_need_status")
    op.drop_column("students", "gender")
    op.alter_column("students", "age", existing_type=sa.Integer(), nullable=False)
