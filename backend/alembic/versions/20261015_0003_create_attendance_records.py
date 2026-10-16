"""Create attendance records.

Revision ID: 20261015_0003
Revises: 20261015_0002
Create Date: 2026-10-15
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20261015_0003"
down_revision: str | None = "20261015_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "attendance_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("attendance_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("note", sa.String(), nullable=True),
        sa.CheckConstraint(
            "status IN ('present', 'absent', 'late', 'excused')",
            name="ck_attendance_status",
        ),
        sa.ForeignKeyConstraint(
            ["class_id"],
            ["classes.id"],
            name="attendance_records_class_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["student_id"],
            ["students.id"],
            name="attendance_records_student_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "class_id",
            "student_id",
            "attendance_date",
            name="uq_attendance_class_student_date",
        ),
    )
    op.create_index(
        "ix_attendance_records_class_date_student",
        "attendance_records",
        ["class_id", "attendance_date", "student_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_attendance_records_class_date_student",
        table_name="attendance_records",
    )
    op.drop_table("attendance_records")
