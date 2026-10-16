from typing import Annotated

from pydantic import StringConstraints
from sqlalchemy import CheckConstraint, Column, String
from sqlmodel import Field, SQLModel

ClassName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]


class ClassroomBase(SQLModel):
    name: ClassName = Field(sa_column=Column(String, nullable=False))
    description: str | None = Field(
        default=None,
        sa_column=Column(String, nullable=True),
    )


class Classroom(ClassroomBase, table=True):
    __tablename__ = "classes"
    __table_args__ = (
        CheckConstraint(
            "char_length(name) >= 1 AND name = btrim(name)",
            name="ck_classes_name_nonblank_trimmed",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)


class ClassCreate(ClassroomBase):
    pass


class ClassUpdate(ClassroomBase):
    pass


class ClassRead(SQLModel):
    id: int
    name: ClassName
    description: str | None = None
