from datetime import date
from enum import Enum

from pydantic import ConfigDict
from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    Index,
    String,
    UniqueConstraint,
)
from sqlmodel import Field, SQLModel


class AttendanceStatus(str, Enum):
    PRESENT = "present"
    ABSENT = "absent"
    LATE = "late"
    EXCUSED = "excused"


class AttendanceCreate(SQLModel):
    student_id: int = Field(gt=0)
    attendance_date: date
    status: AttendanceStatus
    note: str | None = None


class AttendanceUpdate(SQLModel):
    model_config = ConfigDict(extra="forbid")

    status: AttendanceStatus
    note: str | None = None


class AttendanceRecord(SQLModel, table=True):
    __tablename__ = "attendance_records"
    __table_args__ = (
        UniqueConstraint(
            "class_id",
            "student_id",
            "attendance_date",
            name="uq_attendance_class_student_date",
        ),
        CheckConstraint(
            "status IN ('present', 'absent', 'late', 'excused')",
            name="ck_attendance_status",
        ),
        Index(
            "ix_attendance_records_class_date_student",
            "class_id",
            "attendance_date",
            "student_id",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    class_id: int = Field(foreign_key="classes.id", ondelete="RESTRICT")
    student_id: int = Field(foreign_key="students.id", ondelete="RESTRICT")
    attendance_date: date = Field(
        sa_column=Column(Date, nullable=False),
    )
    status: AttendanceStatus = Field(
        sa_column=Column(String(8), nullable=False),
    )
    note: str | None = Field(
        default=None,
        sa_column=Column(String, nullable=True),
    )


class AttendanceRead(SQLModel):
    id: int
    class_id: int
    student_id: int
    attendance_date: date
    status: AttendanceStatus
    note: str | None = None
