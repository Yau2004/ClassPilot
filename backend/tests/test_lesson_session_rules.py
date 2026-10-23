from datetime import date, datetime, time, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.models.lesson_session import (
    ConfirmationStatus,
    LessonLifecycle,
    ParticipantRecordsBatch,
    ParticipantRecordUpdate,
    SessionCreate,
    SessionUpdate,
    hong_kong_today,
    validate_confirmation,
    validate_schedule,
)
from app.models.lesson_session import LessonSession, SessionParticipant
from app.models.student import Student, StudentCreate, StudentGender, StudentUpdate, SupportNeedStatus


def test_student_age_is_optional_and_support_status_is_explicit() -> None:
    student = StudentCreate(
        name="Demo Student",
        gender=StudentGender.OTHER,
        support_need_status=SupportNeedStatus.NONE_REPORTED,
    )

    assert student.age is None
    assert student.support_need_status == SupportNeedStatus.NONE_REPORTED
    assert StudentCreate(
        name="Demo Student",
        gender=StudentGender.OTHER,
        support_need_status=SupportNeedStatus.NONE_REPORTED,
    ).support_need_status == SupportNeedStatus.NONE_REPORTED


def test_student_create_requires_gender_and_an_explicit_support_status() -> None:
    with pytest.raises(ValidationError):
        StudentCreate(name="Demo Student", support_need_status="none_reported")
    with pytest.raises(ValidationError):
        StudentCreate(name="Demo Student", gender="other")
    with pytest.raises(ValidationError):
        StudentCreate(
            name="Demo Student",
            gender="other",
            support_need_status="not_provided",
        )


def test_student_partial_update_preserves_legacy_omissions_and_rejects_nulls() -> None:
    update = StudentUpdate(name="Updated Name")
    assert update.model_dump(exclude_unset=True) == {"name": "Updated Name"}
    with pytest.raises(ValidationError, match="gender cannot be null"):
        StudentUpdate(gender=None)
    with pytest.raises(ValidationError):
        StudentUpdate(support_need_status="not_provided")
    with pytest.raises(ValidationError, match="cannot be null when supplied"):
        StudentUpdate(support_need_status=None)


def test_schema_uses_nullable_age_and_local_date_time_columns() -> None:
    assert Student.__table__.columns["age"].nullable is True
    assert LessonSession.__table__.columns["session_date"].type.__class__.__name__ == "Date"
    assert LessonSession.__table__.columns["start_time"].type.timezone is False
    assert LessonSession.__table__.columns["end_time"].type.timezone is False
    assert any(
        constraint.name == "uq_session_participants_session_student"
        for constraint in SessionParticipant.__table__.constraints
    )


def test_schedule_allows_unassigned_and_date_without_time() -> None:
    unassigned = SessionCreate()
    date_only = SessionCreate(session_date=date(2026, 10, 31))

    assert unassigned.session_date is None
    assert unassigned.start_time is None and unassigned.end_time is None
    assert date_only.session_date == date(2026, 10, 31)
    assert date_only.start_time is None and date_only.end_time is None


@pytest.mark.parametrize(
    ("session_date", "start_time", "end_time"),
    [
        (None, time(9), time(10)),
        (date(2026, 10, 31), time(9), None),
        (date(2026, 10, 31), None, time(10)),
        (date(2026, 10, 31), time(10), time(9)),
        (date(2026, 10, 31), time(9), time(9)),
    ],
)
def test_invalid_schedule_combinations_are_rejected(
    session_date: date | None,
    start_time: time | None,
    end_time: time | None,
) -> None:
    with pytest.raises((ValueError, ValidationError)):
        SessionCreate(
            session_date=session_date,
            start_time=start_time,
            end_time=end_time,
        )


def test_timezone_offset_is_not_accepted_for_local_lesson_time() -> None:
    with pytest.raises(ValidationError):
        SessionCreate(
            session_date=date(2026, 10, 31),
            start_time=time(9, tzinfo=timezone(timedelta(hours=8))),
            end_time=time(10, tzinfo=timezone(timedelta(hours=8))),
        )


def test_duplicate_participants_and_empty_record_patches_are_rejected() -> None:
    with pytest.raises(ValidationError, match="only be added once"):
        SessionCreate(participant_ids=[5, 5])
    with pytest.raises(ValidationError, match="Submit attendance_status"):
        ParticipantRecordUpdate(student_id=5)
    with pytest.raises(ValidationError, match="Use 'unrecorded'"):
        ParticipantRecordUpdate(student_id=5, attendance_status=None)
    with pytest.raises(ValidationError, match="each student only once"):
        ParticipantRecordsBatch(
            participants=[
                ParticipantRecordUpdate(student_id=5, attendance_status="present"),
                ParticipantRecordUpdate(student_id=5, evaluation=None),
            ]
        )


def test_partial_session_update_keeps_omitted_fields_distinct_from_null() -> None:
    omitted = SessionUpdate(notes="updated")
    cleared = SessionUpdate(session_date=None, start_time=None, end_time=None)

    assert omitted.model_fields_set == {"notes"}
    assert cleared.model_fields_set == {"session_date", "start_time", "end_time"}
    with pytest.raises(ValueError, match="without a lesson date"):
        validate_schedule(None, time(9), None)


def test_confirmed_session_requires_complete_schedule_topic_and_participant() -> None:
    with pytest.raises(ValueError, match="requires a date"):
        validate_confirmation(
            date(2026, 10, 31), time(9), time(10), "Roblox Studio", None, [],
            ConfirmationStatus.CONFIRMED, LessonLifecycle.PLANNED,
        )

    validate_confirmation(
        date(2026, 10, 31), time(9), time(10), "Roblox Studio", None, [1],
        ConfirmationStatus.CONFIRMED, LessonLifecycle.PLANNED,
    )


def test_hong_kong_date_changes_at_local_midnight_and_month_boundary() -> None:
    before_midnight = datetime(2026, 10, 31, 15, 59, tzinfo=timezone.utc)
    after_midnight = datetime(2026, 10, 31, 16, 0, tzinfo=timezone.utc)
    year_end = datetime(2026, 12, 31, 16, 0, tzinfo=timezone.utc)

    assert hong_kong_today(before_midnight) == date(2026, 10, 31)
    assert hong_kong_today(after_midnight) == date(2026, 11, 1)
    assert hong_kong_today(year_end) == date(2027, 1, 1)
