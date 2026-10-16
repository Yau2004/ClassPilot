"""Create the students table.

Revision ID: 20261013_0001
Revises:
Create Date: 2026-10-13
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20261013_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "students",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("age", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "char_length(name) >= 1 AND char_length(name) <= 100 "
            "AND name = btrim(name)",
            name="ck_students_name_nonblank_trimmed",
        ),
        sa.CheckConstraint(
            "age >= 4 AND age <= 18",
            name="ck_students_age_range",
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("students")
