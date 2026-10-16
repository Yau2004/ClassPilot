from typing import Annotated

from pydantic import StringConstraints
from sqlalchemy import CheckConstraint, Column, String
from sqlmodel import Field, SQLModel

StudentName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
]


class StudentBase(SQLModel):
    name: StudentName = Field(sa_column=Column(String(100), nullable=False))
    age: int = Field(ge=4, le=18)


class Student(StudentBase, table=True):
    __tablename__ = "students"
    __table_args__ = (
        CheckConstraint(
            "char_length(name) >= 1 AND char_length(name) <= 100 "
            "AND name = btrim(name)",
            name="ck_students_name_nonblank_trimmed",
        ),
        CheckConstraint(
            "age >= 4 AND age <= 18",
            name="ck_students_age_range",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)


class StudentCreate(StudentBase):
    pass


class StudentRead(SQLModel):
    id: int
    name: StudentName
    age: int = Field(ge=4, le=18)
