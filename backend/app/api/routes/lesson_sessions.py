from calendar import monthrange
from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from app.db.session import get_session
from app.models.lesson_session import (
    ConfirmationStatus,
    LessonLifecycle,
    LessonSession,
    MonthSessionsRead,
    OverlapWarning,
    ParticipantRecordsBatch,
    SessionCreate,
    SessionDetail,
    SessionParticipant,
    SessionParticipantRead,
    SessionSummary,
    SessionUpdate,
    validate_confirmation,
    validate_schedule,
)
from app.models.student import Student, StudentProfileRead

router = APIRouter(prefix="/sessions", tags=["lesson sessions"])
SessionDependency = Annotated[Session, Depends(get_session)]


def _require_session(session_id: int, session: Session) -> LessonSession:
    lesson = session.get(LessonSession, session_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail=f"Lesson session {session_id} was not found.")
    return lesson


def _active_participants(session_id: int, session: Session) -> list[SessionParticipant]:
    return list(
        session.exec(
            select(SessionParticipant)
            .where(
                SessionParticipant.session_id == session_id,
                SessionParticipant.removed_at.is_(None),
            )
            .order_by(SessionParticipant.student_id)
        ).all()
    )


def _overlap_warnings(
    lesson: LessonSession,
    participant_ids: list[int],
    session: Session,
) -> list[OverlapWarning]:
    if lesson.session_date is None or lesson.start_time is None or lesson.end_time is None:
        return []
    statement = (
        select(LessonSession)
        .join(SessionParticipant, SessionParticipant.session_id == LessonSession.id)
        .where(
            LessonSession.id != lesson.id,
            LessonSession.session_date == lesson.session_date,
            LessonSession.lifecycle_status == LessonLifecycle.PLANNED,
            SessionParticipant.student_id.in_(participant_ids),
            SessionParticipant.removed_at.is_(None),
            LessonSession.start_time < lesson.end_time,
            LessonSession.end_time > lesson.start_time,
        )
        .distinct()
        .order_by(LessonSession.start_time, LessonSession.id)
    )
    return [
        OverlapWarning(
            session_id=item.id,
            session_date=item.session_date,
            start_time=item.start_time,
            end_time=item.end_time,
        )
        for item in session.exec(statement).all()
    ]


def _to_summaries(
    lessons: list[LessonSession],
    session: Session,
) -> list[SessionSummary]:
    if not lessons:
        return []
    counts = session.exec(
        select(SessionParticipant.session_id, func.count(SessionParticipant.id))
        .where(
            SessionParticipant.session_id.in_([lesson.id for lesson in lessons]),
            SessionParticipant.removed_at.is_(None),
        )
        .group_by(SessionParticipant.session_id)
    ).all()
    active_counts = dict(counts)
    return [
        SessionSummary(
            id=lesson.id,
            course_type=lesson.course_type,
            topic=lesson.topic,
            session_date=lesson.session_date,
            start_time=lesson.start_time,
            end_time=lesson.end_time,
            confirmation_status=lesson.confirmation_status,
            lifecycle_status=lesson.lifecycle_status,
            participant_count=active_counts.get(lesson.id, 0),
        )
        for lesson in lessons
    ]


def _to_summary(lesson: LessonSession, session: Session) -> SessionSummary:
    return _to_summaries([lesson], session)[0]


def _to_detail(lesson: LessonSession, session: Session) -> SessionDetail:
    participant_rows = list(
        session.exec(
            select(SessionParticipant, Student)
            .join(Student, Student.id == SessionParticipant.student_id)
            .where(SessionParticipant.session_id == lesson.id)
            .order_by(SessionParticipant.removed_at, Student.name, Student.id)
        ).all()
    )
    participants = [
        SessionParticipantRead(
            student_id=participant.student_id,
            attendance_status=participant.attendance_status,
            evaluation=participant.evaluation,
            removed_at=participant.removed_at,
            student=StudentProfileRead.model_validate(student),
        )
        for participant, student in participant_rows
    ]
    active_ids = [participant.student_id for participant, _ in participant_rows if participant.removed_at is None]
    summary = _to_summary(lesson, session)
    return SessionDetail(
        **summary.model_dump(),
        notes=lesson.notes,
        participants=participants,
        overlap_warnings=_overlap_warnings(lesson, active_ids, session),
    )


def _validate_student_ids(ids: list[int], session: Session) -> None:
    if not ids:
        return
    students = list(session.exec(select(Student).where(Student.id.in_(ids))).all())
    found = {student.id: student for student in students}
    missing = [student_id for student_id in ids if student_id not in found]
    if missing:
        raise HTTPException(status_code=404, detail=f"Student IDs not found: {missing}.")
    archived = [student_id for student_id in ids if found[student_id].is_archived]
    if archived:
        raise HTTPException(status_code=409, detail=f"Archived students cannot be added: {archived}.")


def _apply_membership(
    lesson: LessonSession,
    wanted_ids: list[int],
    session: Session,
) -> None:
    existing = list(
        session.exec(
            select(SessionParticipant).where(SessionParticipant.session_id == lesson.id)
        ).all()
    )
    by_student = {participant.student_id: participant for participant in existing}
    wanted = set(wanted_ids)
    now = datetime.now(timezone.utc)
    for participant in existing:
        if participant.student_id not in wanted and participant.removed_at is None:
            participant.removed_at = now
            session.add(participant)
        elif participant.student_id in wanted and participant.removed_at is not None:
            participant.removed_at = None
            session.add(participant)
    for student_id in wanted_ids:
        if student_id not in by_student:
            session.add(SessionParticipant(session_id=lesson.id, student_id=student_id))


@router.get("/month", response_model=MonthSessionsRead)
def list_month_sessions(
    session: SessionDependency,
    month: Annotated[str, Query(pattern=r"^\d{4}-\d{2}$")],
) -> MonthSessionsRead:
    year, month_number = (int(value) for value in month.split("-"))
    if not 1 <= year <= 9999 or not 1 <= month_number <= 12:
        raise HTTPException(status_code=422, detail="month must be a valid YYYY-MM calendar month.")
    first_day = date(year, month_number, 1)
    last_day = date(year, month_number, monthrange(year, month_number)[1])
    lessons = session.exec(
        select(LessonSession)
        .where(LessonSession.session_date >= first_day, LessonSession.session_date <= last_day)
        .order_by(LessonSession.session_date, LessonSession.start_time, LessonSession.id)
    ).all()
    return MonthSessionsRead(
        month=month,
        timezone="Asia/Hong_Kong",
        sessions=_to_summaries(list(lessons), session),
    )


@router.get("/unassigned", response_model=list[SessionSummary])
def list_unassigned_sessions(session: SessionDependency) -> list[SessionSummary]:
    lessons = session.exec(
        select(LessonSession)
        .where(LessonSession.session_date.is_(None))
        .order_by(LessonSession.created_at, LessonSession.id)
    ).all()
    return _to_summaries(list(lessons), session)


@router.get("/range", response_model=list[SessionSummary])
def list_session_range(
    session: SessionDependency,
    start_date: date,
    end_date: date,
) -> list[SessionSummary]:
    if start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must be on or before end_date.")
    if (end_date - start_date).days > 366:
        raise HTTPException(status_code=422, detail="Date range cannot exceed 367 calendar days.")
    lessons = session.exec(
        select(LessonSession)
        .where(LessonSession.session_date >= start_date, LessonSession.session_date <= end_date)
        .order_by(LessonSession.session_date, LessonSession.start_time, LessonSession.id)
    ).all()
    return _to_summaries(list(lessons), session)


@router.post("", response_model=SessionDetail, status_code=status.HTTP_201_CREATED)
def create_session(session_data: SessionCreate, session: SessionDependency) -> SessionDetail:
    participant_ids = session_data.participant_ids
    try:
        _validate_student_ids(participant_ids, session)
        validate_confirmation(
            session_data.session_date,
            session_data.start_time,
            session_data.end_time,
            session_data.course_type,
            session_data.topic,
            participant_ids,
            session_data.confirmation_status,
            session_data.lifecycle_status,
        )
        lesson = LessonSession(
            **session_data.model_dump(exclude={"participant_ids"})
        )
        session.add(lesson)
        session.flush()
        _apply_membership(lesson, participant_ids, session)
        session.commit()
        session.refresh(lesson)
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="Lesson conflicts with existing data.") from None
    except ValueError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from None
    return _to_detail(lesson, session)


@router.get("/{session_id}", response_model=SessionDetail)
def get_session_detail(session_id: int, session: SessionDependency) -> SessionDetail:
    return _to_detail(_require_session(session_id, session), session)


@router.patch("/{session_id}", response_model=SessionDetail)
def update_session(
    session_id: int,
    session_data: SessionUpdate,
    session: SessionDependency,
) -> SessionDetail:
    lesson = _require_session(session_id, session)
    if lesson.lifecycle_status != LessonLifecycle.PLANNED:
        raise HTTPException(status_code=409, detail="Cancelled or completed lessons cannot be edited.")
    values = session_data.model_dump(exclude_unset=True)
    for required_status in ("confirmation_status", "lifecycle_status"):
        if required_status in values and values[required_status] is None:
            raise HTTPException(status_code=422, detail=f"{required_status} cannot be null.")
    if "participant_ids" in session_data.model_fields_set and session_data.participant_ids is None:
        raise HTTPException(status_code=422, detail="Use an empty list to remove all active participants.")
    participant_ids = values.pop("participant_ids", None)
    if not values and participant_ids is None:
        raise HTTPException(status_code=422, detail="At least one lesson field is required.")
    if participant_ids is None:
        participant_ids = [participant.student_id for participant in _active_participants(session_id, session)]
    try:
        _validate_student_ids(
            [student_id for student_id in participant_ids if student_id not in {row.student_id for row in _active_participants(session_id, session)}],
            session,
        )
        proposed = {
            "session_date": lesson.session_date,
            "start_time": lesson.start_time,
            "end_time": lesson.end_time,
            "course_type": lesson.course_type,
            "topic": lesson.topic,
            "confirmation_status": lesson.confirmation_status,
            "lifecycle_status": lesson.lifecycle_status,
            **values,
        }
        validate_schedule(proposed["session_date"], proposed["start_time"], proposed["end_time"])
        validate_confirmation(
            proposed["session_date"],
            proposed["start_time"],
            proposed["end_time"],
            proposed["course_type"],
            proposed["topic"],
            participant_ids,
            proposed["confirmation_status"],
            proposed["lifecycle_status"],
        )
        for field, value in values.items():
            setattr(lesson, field, value)
        lesson.updated_at = datetime.now(timezone.utc)
        _apply_membership(lesson, participant_ids, session)
        session.add(lesson)
        session.commit()
        session.refresh(lesson)
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="Lesson conflicts with existing data.") from None
    except ValueError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from None
    return _to_detail(lesson, session)


@router.patch("/{session_id}/participant-records", response_model=SessionDetail)
def update_participant_records(
    session_id: int,
    batch: ParticipantRecordsBatch,
    session: SessionDependency,
) -> SessionDetail:
    lesson = _require_session(session_id, session)
    if lesson.lifecycle_status == LessonLifecycle.CANCELLED:
        raise HTTPException(status_code=409, detail="Attendance cannot be recorded for a cancelled lesson.")
    active = {item.student_id: item for item in _active_participants(session_id, session)}
    missing = [item.student_id for item in batch.participants if item.student_id not in active]
    if missing:
        raise HTTPException(status_code=409, detail=f"Students are not active participants: {missing}.")
    try:
        for update in batch.participants:
            record = active[update.student_id]
            values = update.model_dump(exclude_unset=True)
            values.pop("student_id")
            for field, value in values.items():
                setattr(record, field, value)
            session.add(record)
        session.commit()
    except (IntegrityError, SQLAlchemyError):
        session.rollback()
        raise HTTPException(status_code=409, detail="No participant records were saved; retry the full batch.") from None
    return _to_detail(lesson, session)
