from enum import Enum
from typing import Annotated

from pydantic import ConfigDict, StringConstraints, model_validator
from sqlalchemy import Boolean, CheckConstraint, Column, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlmodel import Field, SQLModel

StudentName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
]


class StudentGender(str, Enum):
    FEMALE = "female"
    MALE = "male"
    OTHER = "other"
    PREFER_NOT_TO_SAY = "prefer_not_to_say"


class SupportNeedStatus(str, Enum):
    NOT_PROVIDED = "not_provided"
    NONE_REPORTED = "none_reported"
    NEEDS_SUPPORT = "needs_support"


class StudentSupportNeedChoice(str, Enum):
    NONE_REPORTED = "none_reported"
    NEEDS_SUPPORT = "needs_support"


class StudentBase(SQLModel):
    name: StudentName = Field(sa_column=Column(String(100), nullable=False))
    age: int | None = Field(default=None, ge=4, le=18)


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
        CheckConstraint(
            "gender IS NULL OR gender IN "
            "('female', 'male', 'other', 'prefer_not_to_say')",
            name="ck_students_gender",
        ),
        CheckConstraint(
            "support_need_status IN "
            "('not_provided', 'none_reported', 'needs_support')",
            name="ck_students_support_need_status",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    gender: StudentGender | None = Field(
        default=None,
        sa_column=Column(String(24), nullable=True),
    )
    support_need_status: SupportNeedStatus = Field(
        default=SupportNeedStatus.NOT_PROVIDED,
        sa_column=Column(
            String(20),
            nullable=False,
            server_default="not_provided",
        ),
    )
    support_need_tags: list[str] = Field(
        default_factory=list,
        sa_column=Column(
            ARRAY(String()),
            nullable=False,
            server_default=text("ARRAY[]::VARCHAR[]"),
        ),
    )
    support_notes: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    general_notes: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    is_archived: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("false")),
    )


class StudentCreate(StudentBase):
    model_config = ConfigDict(extra="forbid")

    gender: StudentGender
    support_need_status: StudentSupportNeedChoice
    support_need_tags: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]] = Field(default_factory=list, max_length=30)
    support_notes: str | None = Field(default=None, max_length=4000)
    general_notes: str | None = Field(default=None, max_length=4000)

class StudentUpdate(SQLModel):
    model_config = ConfigDict(extra="forbid")

    name: StudentName | None = None
    age: int | None = Field(default=None, ge=4, le=18)
    gender: StudentGender | None = None
    support_need_status: StudentSupportNeedChoice | None = None
    support_need_tags: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]] | None = Field(default=None, max_length=30)
    support_notes: str | None = Field(default=None, max_length=4000)
    general_notes: str | None = Field(default=None, max_length=4000)
    is_archived: bool | None = None

    @model_validator(mode="after")
    def validate_explicit_profile_updates(self) -> "StudentUpdate":
        supplied = self.model_fields_set
        if "gender" in supplied and self.gender is None:
            raise ValueError("gender cannot be null when supplied.")
        if "support_need_status" in supplied and self.support_need_status is None:
            raise ValueError("support_need_status cannot be null when supplied.")
        return self


class StudentRead(SQLModel):
    id: int
    name: StudentName
    age: int | None = Field(default=None, ge=4, le=18)


class StudentListRead(StudentRead):
    is_archived: bool


class StudentProfileRead(StudentRead):
    gender: StudentGender | None = None
    support_need_status: SupportNeedStatus
    support_need_tags: list[str]
    support_notes: str | None = None
    general_notes: str | None = None
    is_archived: bool
