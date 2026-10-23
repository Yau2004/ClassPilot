from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.db.session import get_session
from app.models import AttendanceRecord, Student, StudentClassEnrollment
from app.models.lesson_session import (
    LessonSession,
    SessionParticipant,
    StudentSessionRead,
)
from app.models.student import (
    StudentCreate,
    StudentListRead,
    StudentProfileRead,
    StudentRead,
    StudentUpdate,
)

router = APIRouter(prefix="/students", tags=["students"])
SessionDependency = Annotated[Session, Depends(get_session)]


def require_student(student_id: int, session: Session) -> Student:
    student = session.get(Student, student_id)
    if student is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Student with id {student_id} was not found.",
        )
    return student


def _apply_student_update(student: Student, update: StudentUpdate) -> None:
    values = update.model_dump(exclude_unset=True)
    if "name" in values and values["name"] is None:
        raise HTTPException(status_code=422, detail="name cannot be null.")
    if "support_need_status" in values and values["support_need_status"] is None:
        raise HTTPException(status_code=422, detail="support_need_status cannot be null.")
    if "support_need_tags" in values and values["support_need_tags"] is None:
        raise HTTPException(status_code=422, detail="Use an empty list to clear support_need_tags.")
    if "is_archived" in values and values["is_archived"] is None:
        raise HTTPException(status_code=422, detail="is_archived cannot be null.")
    for field, value in values.items():
        setattr(student, field, value)


@router.post("", response_model=StudentRead, status_code=status.HTTP_201_CREATED)
def create_student(student_data: StudentCreate, session: SessionDependency) -> Student:
    student = Student.model_validate(student_data.model_dump())
    session.add(student)
    session.commit()
    session.refresh(student)
    return student


@router.get("", response_model=list[StudentListRead])
def list_students(
    session: SessionDependency,
    include_archived: bool = False,
) -> list[Student]:
    statement = select(Student)
    if not include_archived:
        statement = statement.where(Student.is_archived.is_(False))
    return list(session.exec(statement.order_by(Student.id)).all())


@router.get("/profiles", response_model=list[StudentProfileRead])
def list_student_profiles(
    session: SessionDependency,
    include_archived: bool = False,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
) -> list[Student]:
    """Return student profiles in bounded batches for screens that need them."""
    statement = select(Student)
    if not include_archived:
        statement = statement.where(Student.is_archived.is_(False))
    statement = statement.order_by(Student.id).offset(offset).limit(limit)
    return list(session.exec(statement).all())


@router.get("/{student_id}/profile", response_model=StudentProfileRead)
def get_student_profile(student_id: int, session: SessionDependency) -> Student:
    return require_student(student_id, session)


@router.get("/{student_id}/sessions", response_model=list[StudentSessionRead])
def list_student_sessions(
    student_id: int,
    session: SessionDependency,
) -> list[StudentSessionRead]:
    require_student(student_id, session)
    rows = list(
        session.exec(
            select(LessonSession, SessionParticipant)
            .join(
                SessionParticipant,
                SessionParticipant.session_id == LessonSession.id,
            )
            .where(SessionParticipant.student_id == student_id)
            .order_by(
                LessonSession.session_date,
                LessonSession.start_time,
                LessonSession.id,
            )
        ).all()
    )
    if not rows:
        return []

    lesson_ids = [lesson.id for lesson, _ in rows]
    active_counts = dict(
        session.exec(
            select(SessionParticipant.session_id, func.count(SessionParticipant.id))
            .where(
                SessionParticipant.session_id.in_(lesson_ids),
                SessionParticipant.removed_at.is_(None),
            )
            .group_by(SessionParticipant.session_id)
        ).all()
    )
    return [
        StudentSessionRead(
            id=lesson.id,
            course_type=lesson.course_type,
            topic=lesson.topic,
            session_date=lesson.session_date,
            start_time=lesson.start_time,
            end_time=lesson.end_time,
            confirmation_status=lesson.confirmation_status,
            lifecycle_status=lesson.lifecycle_status,
            participant_count=active_counts.get(lesson.id, 0),
            attendance_status=participant.attendance_status,
            evaluation=participant.evaluation,
            participant_removed=participant.removed_at is not None,
        )
        for lesson, participant in rows
    ]


@router.get("/{student_id}", response_model=StudentRead)
def get_student(student_id: int, session: SessionDependency) -> Student:
    return require_student(student_id, session)


@router.put("/{student_id}", response_model=StudentRead)
@router.patch("/{student_id}", response_model=StudentRead)
def update_student(
    student_id: int,
    student_data: StudentUpdate,
    session: SessionDependency,
) -> Student:
    if not student_data.model_fields_set:
        raise HTTPException(status_code=422, detail="At least one student field is required.")
    student = require_student(student_id, session)
    _apply_student_update(student, student_data)
    session.add(student)
    session.commit()
    session.refresh(student)
    return student


@router.post("/{student_id}/archive", response_model=StudentProfileRead)
def archive_student(student_id: int, session: SessionDependency) -> Student:
    student = require_student(student_id, session)
    student.is_archived = True
    session.add(student)
    session.commit()
    session.refresh(student)
    return student


@router.post("/{student_id}/restore", response_model=StudentProfileRead)
def restore_student(student_id: int, session: SessionDependency) -> Student:
    student = require_student(student_id, session)
    student.is_archived = False
    session.add(student)
    session.commit()
    session.refresh(student)
    return student


@router.delete(
    "/{student_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses={404: {"description": "Student not found."}, 409: {"description": "Student has preserved history."}},
)
def delete_student(student_id: int, session: SessionDependency) -> Response:
    student = require_student(student_id, session)
    historical_records = (
        session.exec(
            select(StudentClassEnrollment.student_id)
            .where(StudentClassEnrollment.student_id == student_id)
            .limit(1)
        ).first()
        or session.exec(
            select(AttendanceRecord.id)
            .where(AttendanceRecord.student_id == student_id)
            .limit(1)
        ).first()
        or session.exec(
            select(SessionParticipant.id)
            .where(SessionParticipant.student_id == student_id)
            .limit(1)
        ).first()
    )
    if historical_records is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Student with id {student_id} has enrollment or lesson history; archive the student to retain it.",
        )

    session.delete(student)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Student with id {student_id} has preserved history; archive the student to retain it.",
        ) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)
