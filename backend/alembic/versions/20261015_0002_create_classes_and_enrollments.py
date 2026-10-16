"""Create classes and student class enrollments.

Revision ID: 20261015_0002
Revises: 20261013_0001
Create Date: 2026-10-15
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20261015_0002"
down_revision: str | None = "20261013_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "classes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.CheckConstraint(
            "char_length(name) >= 1 AND name = btrim(name)",
            name="ck_classes_name_nonblank_trimmed",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "student_class_enrollments",
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["class_id"],
            ["classes.id"],
            name="student_class_enrollments_class_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["student_id"],
            ["students.id"],
            name="student_class_enrollments_student_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "student_id",
            "class_id",
            name="pk_student_class_enrollments",
        ),
    )
    op.create_index(
        "ix_student_class_enrollments_class_id",
        "student_class_enrollments",
        ["class_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_student_class_enrollments_class_id",
        table_name="student_class_enrollments",
    )
    op.drop_table("student_class_enrollments")
    op.drop_table("classes")
