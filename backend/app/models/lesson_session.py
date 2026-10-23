from datetime import date, datetime, time, timezone
from enum import Enum
from typing import Annotated
from zoneinfo import ZoneInfo

from pydantic import ConfigDict, StringConstraints, field_validator, model_validator
from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlmodel import Field, SQLModel

from app.models.student import StudentProfileRead

CourseLabel = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
]
HONG_KONG_TIMEZONE = ZoneInfo("Asia/Hong_Kong")


def hong_kong_today(moment: datetime | None = None) -> date:
    instant = moment or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError("moment must be timezone-aware.")
    return instant.astimezone(HONG_KONG_TIMEZONE).date()


class ConfirmationStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"


class LessonLifecycle(str, Enum):
    PLANNED = "planned"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class SessionAttendanceStatus(str, Enum):
    UNRECORDED = "unrecorded"
    PRESENT = "present"
    ABSENT = "absent"
    LATE = "late"
    EXCUSED = "excused"


class LessonSession(SQLModel, table=True):
    __tablename__ = "lesson_sessions"
    __table_args__ = (
        CheckConstraint(
            "(session_date IS NOT NULL OR "
            "(start_time IS NULL AND end_time IS NULL))",
            name="ck_lesson_sessions_times_require_date",
        ),
        CheckConstraint(
            "(start_time IS NULL AND end_time IS NULL) OR "
            "(start_time IS NOT NULL AND end_time IS NOT NULL "
            "AND start_time < end_time)",
            name="ck_lesson_sessions_time_pair_and_order",
        ),
        CheckConstraint(
            "confirmation_status IN ('pending', 'confirmed')",
            name="ck_lesson_sessions_confirmation_status",
        ),
        CheckConstraint(
            "lifecycle_status IN ('planned', 'cancelled', 'completed')",
            name="ck_lesson_sessions_lifecycle_status",
        ),
        Index("ix_lesson_sessions_date_status", "session_date", "lifecycle_status"),
    )

    id: int | None = Field(default=None, primary_key=True)
    course_type: str | None = Field(
        default=None,
        sa_column=Column(String(100), nullable=True),
    )
    topic: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    session_date: date | None = Field(
        default=None,
        sa_column=Column(Date, nullable=True),
    )
    start_time: time | None = Field(
        default=None,
        sa_column=Column(Time(timezone=False), nullable=True),
    )
    end_time: time | None = Field(
        default=None,
        sa_column=Column(Time(timezone=False), nullable=True),
    )
    confirmation_status: ConfirmationStatus = Field(
        default=ConfirmationStatus.PENDING,
        sa_column=Column(String(12), nullable=False, server_default="pending"),
    )
    lifecycle_status: LessonLifecycle = Field(
        default=LessonLifecycle.PLANNED,
        sa_column=Column(String(12), nullable=False, server_default="planned"),
    )
    notes: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )


class SessionParticipant(SQLModel, table=True):
    __tablename__ = "session_participants"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "student_id",
            name="uq_session_participants_session_student",
        ),
        CheckConstraint(
            "attendance_status IN "
            "('unrecorded', 'present', 'absent', 'late', 'excused')",
            name="ck_session_participants_attendance_status",
        ),
        Index("ix_session_participants_student", "student_id", "session_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(
        sa_column=Column(
            ForeignKey("lesson_sessions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
    )
    student_id: int = Field(
        sa_column=Column(ForeignKey("students.id", ondelete="RESTRICT"), nullable=False),
    )
    attendance_status: SessionAttendanceStatus = Field(
        default=SessionAttendanceStatus.UNRECORDED,
        sa_column=Column(String(12), nullable=False, server_default="unrecorded"),
    )
    evaluation: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    removed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


class SessionCreate(SQLModel):
    model_config = ConfigDict(extra="forbid")

    course_type: CourseLabel | None = None
    topic: str | None = Field(default=None, max_length=4000)
    session_date: date | None = None
    start_time: time | None = None
    end_time: time | None = None
    confirmation_status: ConfirmationStatus = ConfirmationStatus.PENDING
    lifecycle_status: LessonLifecycle = LessonLifecycle.PLANNED
    notes: str | None = Field(default=None, max_length=10000)
    participant_ids: list[int] = Field(default_factory=list, max_length=100)

    @field_validator("start_time", "end_time")
    @classmethod
    def reject_timezone_offsets(cls, value: time | None) -> time | None:
        if value is not None and value.tzinfo is not None:
            raise ValueError("Times must be local Asia/Hong_Kong wall times without an offset.")
        return value

    @field_validator("participant_ids")
    @classmethod
    def participant_ids_must_be_unique(cls, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            raise ValueError("A student can only be added once to a lesson session.")
        if any(student_id <= 0 for student_id in value):
            raise ValueError("Student IDs must be positive.")
        return value

    @model_validator(mode="after")
    def validate_schedule_fields(self):
        validate_schedule(self.session_date, self.start_time, self.end_time)
        return self


class SessionUpdate(SQLModel):
    model_config = ConfigDict(extra="forbid")

    course_type: CourseLabel | None = None
    topic: str | None = Field(default=None, max_length=4000)
    session_date: date | None = None
    start_time: time | None = None
    end_time: time | None = None
    confirmation_status: ConfirmationStatus | None = None
    lifecycle_status: LessonLifecycle | None = None
    notes: str | None = Field(default=None, max_length=10000)
    participant_ids: list[int] | None = Field(default=None, max_length=100)

    @field_validator("start_time", "end_time")
    @classmethod
    def reject_timezone_offsets(cls, value: time | None) -> time | None:
        if value is not None and value.tzinfo is not None:
            raise ValueError("Times must be local Asia/Hong_Kong wall times without an offset.")
        return value

    @field_validator("participant_ids")
    @classmethod
    def participant_ids_must_be_unique(cls, value: list[int] | None):
        if value is not None and len(value) != len(set(value)):
            raise ValueError("A student can only be added once to a lesson session.")
        if value is not None and any(student_id <= 0 for student_id in value):
            raise ValueError("Student IDs must be positive.")
        return value


class ParticipantRecordUpdate(SQLModel):
    model_config = ConfigDict(extra="forbid")

    student_id: int = Field(gt=0)
    attendance_status: SessionAttendanceStatus | None = None
    evaluation: str | None = Field(default=None, max_length=10000)

    @model_validator(mode="after")
    def at_least_one_field_is_submitted(self):
        if not ({"attendance_status", "evaluation"} & self.model_fields_set):
            raise ValueError("Submit attendance_status, evaluation, or both.")
        if "attendance_status" in self.model_fields_set and self.attendance_status is None:
            raise ValueError("Use 'unrecorded' to clear attendance_status.")
        return self


class ParticipantRecordsBatch(SQLModel):
    model_config = ConfigDict(extra="forbid")

    participants: list[ParticipantRecordUpdate] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_student_ids(self):
        ids = [record.student_id for record in self.participants]
        if len(ids) != len(set(ids)):
            raise ValueError("A batch can contain each student only once.")
        return self


class SessionParticipantRead(SQLModel):
    student_id: int
    attendance_status: SessionAttendanceStatus
    evaluation: str | None
    removed_at: datetime | None
    student: StudentProfileRead


class OverlapWarning(SQLModel):
    session_id: int
    session_date: date
    start_time: time
    end_time: time


class SessionSummary(SQLModel):
    id: int
    course_type: str | None
    topic: str | None
    session_date: date | None
    start_time: time | None
    end_time: time | None
    confirmation_status: ConfirmationStatus
    lifecycle_status: LessonLifecycle
    participant_count: int


class StudentSessionRead(SessionSummary):
    attendance_status: SessionAttendanceStatus
    evaluation: str | None
    participant_removed: bool


class SessionDetail(SessionSummary):
    notes: str | None
    participants: list[SessionParticipantRead]
    overlap_warnings: list[OverlapWarning]


class MonthSessionsRead(SQLModel):
    month: str
    timezone: str
    sessions: list[SessionSummary]


def validate_schedule(
    session_date: date | None,
    start_time: time | None,
    end_time: time | None,
) -> None:
    if session_date is None and (start_time is not None or end_time is not None):
        raise ValueError("A time cannot be saved without a lesson date.")
    if (start_time is None) != (end_time is None):
        raise ValueError("Start and end times must both be supplied or both be empty.")
    if start_time is not None and end_time is not None and start_time >= end_time:
        raise ValueError("Lesson start_time must be earlier than end_time.")


def validate_confirmation(
    session_date: date | None,
    start_time: time | None,
    end_time: time | None,
    course_type: str | None,
    topic: str | None,
    participant_ids: list[int],
    confirmation_status: ConfirmationStatus,
    lifecycle_status: LessonLifecycle,
) -> None:
    if confirmation_status == ConfirmationStatus.CONFIRMED or lifecycle_status == LessonLifecycle.COMPLETED:
        complete = (
            session_date is not None
            and start_time is not None
            and end_time is not None
            and bool((course_type or "").strip() or (topic or "").strip())
            and bool(participant_ids)
        )
        if not complete:
            raise ValueError(
                "A confirmed or completed lesson requires a date, start and end time, "
                "a course type or topic, and at least one student."
            )
    if lifecycle_status == LessonLifecycle.COMPLETED and confirmation_status != ConfirmationStatus.CONFIRMED:
        raise ValueError("A lesson must be confirmed before it can be completed.")
