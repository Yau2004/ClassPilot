from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.db.session import get_session
from app.models import (
    AttendanceRecord,
    Student,
    StudentClassEnrollment,
    StudentCreate,
    StudentRead,
)

router = APIRouter(prefix="/students", tags=["students"])
SessionDependency = Annotated[Session, Depends(get_session)]


@router.post(
    "",
    response_model=StudentRead,
    status_code=status.HTTP_201_CREATED,
)
def create_student(
    student_data: StudentCreate,
    session: SessionDependency,
) -> Student:
    student = Student.model_validate(student_data.model_dump())
    session.add(student)
    session.commit()
    session.refresh(student)
    return student


@router.get("", response_model=list[StudentRead])
def list_students(session: SessionDependency) -> list[Student]:
    statement = select(Student).order_by(Student.id)
    return list(session.exec(statement).all())


@router.get(
    "/{student_id}",
    response_model=StudentRead,
    responses={404: {"description": "Student not found."}},
)
def get_student(student_id: int, session: SessionDependency) -> Student:
    student = session.get(Student, student_id)
    if student is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Student with id {student_id} was not found.",
        )
    return student


@router.put(
    "/{student_id}",
    response_model=StudentRead,
    responses={404: {"description": "Student not found."}},
)
def update_student(
    student_id: int,
    student_data: StudentCreate,
    session: SessionDependency,
) -> Student:
    student = session.get(Student, student_id)
    if student is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Student with id {student_id} was not found.",
        )

    student.name = student_data.name
    student.age = student_data.age
    session.add(student)
    session.commit()
    session.refresh(student)
    return student


@router.delete(
    "/{student_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses={404: {"description": "Student not found."}},
)
def delete_student(student_id: int, session: SessionDependency) -> Response:
    student = session.get(Student, student_id)
    if student is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Student with id {student_id} was not found.",
        )

    enrollment = session.exec(
        select(StudentClassEnrollment)
        .where(StudentClassEnrollment.student_id == student_id)
        .limit(1)
    ).first()
    if enrollment is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Student with id {student_id} still has class enrollments.",
        )

    attendance = session.exec(
        select(AttendanceRecord)
        .where(AttendanceRecord.student_id == student_id)
        .limit(1)
    ).first()
    if attendance is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Student with id {student_id} still has attendance records.",
        )

    session.delete(student)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Student with id {student_id} still has enrollments "
                "or attendance records."
            ),
        ) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)
