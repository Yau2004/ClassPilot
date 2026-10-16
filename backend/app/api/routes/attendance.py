from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.db.session import get_session
from app.models import (
    AttendanceCreate,
    AttendanceRead,
    AttendanceRecord,
    AttendanceUpdate,
    Classroom,
    Student,
    StudentClassEnrollment,
)

router = APIRouter(prefix="/classes/{class_id}/attendance", tags=["attendance"])
SessionDependency = Annotated[Session, Depends(get_session)]


def require_classroom(class_id: int, session: Session) -> Classroom:
    classroom = session.get(Classroom, class_id)
    if classroom is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Class with id {class_id} was not found.",
        )
    return classroom


def require_student(student_id: int, session: Session) -> Student:
    student = session.get(Student, student_id)
    if student is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Student with id {student_id} was not found.",
        )
    return student


def raise_attendance_not_found(attendance_id: int) -> None:
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Attendance record with id {attendance_id} was not found.",
    )


@router.post(
    "",
    response_model=AttendanceRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"description": "Class or student not found."},
        409: {"description": "Student is not enrolled or record already exists."},
    },
)
def create_attendance(
    class_id: int,
    attendance_data: AttendanceCreate,
    session: SessionDependency,
) -> AttendanceRecord:
    require_classroom(class_id, session)
    require_student(attendance_data.student_id, session)

    enrollment = session.get(
        StudentClassEnrollment,
        (attendance_data.student_id, class_id),
    )
    if enrollment is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Student {attendance_data.student_id} is not enrolled "
                f"in class {class_id}."
            ),
        )

    record = AttendanceRecord(
        class_id=class_id,
        student_id=attendance_data.student_id,
        attendance_date=attendance_data.attendance_date,
        status=attendance_data.status,
        note=attendance_data.note,
    )
    session.add(record)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        if session.get(Classroom, class_id) is None:
            require_classroom(class_id, session)
        if session.get(Student, attendance_data.student_id) is None:
            require_student(attendance_data.student_id, session)
        if (
            session.get(
                StudentClassEnrollment,
                (attendance_data.student_id, class_id),
            )
            is None
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Student {attendance_data.student_id} is not enrolled "
                    f"in class {class_id}."
                ),
            ) from None
        duplicate = session.exec(
            select(AttendanceRecord.id).where(
                AttendanceRecord.class_id == class_id,
                AttendanceRecord.student_id == attendance_data.student_id,
                AttendanceRecord.attendance_date
                == attendance_data.attendance_date,
            )
        ).first()
        if duplicate is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "An attendance record already exists for this student, "
                    "class, and date."
                ),
            ) from None
        raise

    session.refresh(record)
    return record


@router.get("", response_model=list[AttendanceRead])
def list_attendance(
    class_id: int,
    session: SessionDependency,
    attendance_date: date | None = None,
    student_id: Annotated[int | None, Query(gt=0)] = None,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[AttendanceRecord]:
    require_classroom(class_id, session)
    statement = select(AttendanceRecord).where(
        AttendanceRecord.class_id == class_id
    )
    if attendance_date is not None:
        statement = statement.where(
            AttendanceRecord.attendance_date == attendance_date
        )
    if student_id is not None:
        statement = statement.where(AttendanceRecord.student_id == student_id)
    statement = (
        statement.order_by(
            AttendanceRecord.attendance_date,
            AttendanceRecord.student_id,
            AttendanceRecord.id,
        )
        .offset(offset)
        .limit(limit)
    )
    return list(session.exec(statement).all())


@router.put(
    "/{attendance_id}",
    response_model=AttendanceRead,
    responses={404: {"description": "Class or attendance record not found."}},
)
def update_attendance(
    class_id: int,
    attendance_id: int,
    attendance_data: AttendanceUpdate,
    session: SessionDependency,
) -> AttendanceRecord:
    require_classroom(class_id, session)
    record = session.get(AttendanceRecord, attendance_id)
    if record is None or record.class_id != class_id:
        raise_attendance_not_found(attendance_id)

    record.status = attendance_data.status
    record.note = attendance_data.note
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


@router.delete(
    "/{attendance_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses={404: {"description": "Class or attendance record not found."}},
)
def delete_attendance(
    class_id: int,
    attendance_id: int,
    session: SessionDependency,
) -> Response:
    require_classroom(class_id, session)
    record = session.get(AttendanceRecord, attendance_id)
    if record is None or record.class_id != class_id:
        raise_attendance_not_found(attendance_id)

    session.delete(record)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
