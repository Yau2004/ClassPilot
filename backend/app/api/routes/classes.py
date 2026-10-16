from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.db.session import get_session
from app.models import (
    AttendanceRecord,
    ClassCreate,
    ClassRead,
    Classroom,
    ClassUpdate,
    Student,
    StudentClassEnrollment,
    StudentRead,
)

router = APIRouter(prefix="/classes", tags=["classes"])
SessionDependency = Annotated[Session, Depends(get_session)]


def get_classroom_or_404(class_id: int, session: Session) -> Classroom:
    classroom = session.get(Classroom, class_id)
    if classroom is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Class with id {class_id} was not found.",
        )
    return classroom


def get_student_or_404(student_id: int, session: Session) -> Student:
    student = session.get(Student, student_id)
    if student is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Student with id {student_id} was not found.",
        )
    return student


@router.post("", response_model=ClassRead, status_code=status.HTTP_201_CREATED)
def create_class(
    class_data: ClassCreate,
    session: SessionDependency,
) -> Classroom:
    classroom = Classroom.model_validate(class_data.model_dump())
    session.add(classroom)
    session.commit()
    session.refresh(classroom)
    return classroom


@router.get("", response_model=list[ClassRead])
def list_classes(
    session: SessionDependency,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[Classroom]:
    statement = (
        select(Classroom)
        .order_by(Classroom.id)
        .offset(offset)
        .limit(limit)
    )
    return list(session.exec(statement).all())


@router.get(
    "/{class_id}",
    response_model=ClassRead,
    responses={404: {"description": "Class not found."}},
)
def get_class(class_id: int, session: SessionDependency) -> Classroom:
    return get_classroom_or_404(class_id, session)


@router.put(
    "/{class_id}",
    response_model=ClassRead,
    responses={404: {"description": "Class not found."}},
)
def update_class(
    class_id: int,
    class_data: ClassUpdate,
    session: SessionDependency,
) -> Classroom:
    classroom = get_classroom_or_404(class_id, session)
    classroom.name = class_data.name
    classroom.description = class_data.description
    session.add(classroom)
    session.commit()
    session.refresh(classroom)
    return classroom


@router.delete(
    "/{class_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses={
        404: {"description": "Class not found."},
        409: {"description": "Class has enrollments or attendance records."},
    },
)
def delete_class(class_id: int, session: SessionDependency) -> Response:
    classroom = get_classroom_or_404(class_id, session)
    enrollment = session.exec(
        select(StudentClassEnrollment)
        .where(StudentClassEnrollment.class_id == class_id)
        .limit(1)
    ).first()
    if enrollment is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Class with id {class_id} still has student enrollments.",
        )

    attendance = session.exec(
        select(AttendanceRecord)
        .where(AttendanceRecord.class_id == class_id)
        .limit(1)
    ).first()
    if attendance is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Class with id {class_id} still has attendance records.",
        )

    session.delete(classroom)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Class with id {class_id} still has enrollments "
                "or attendance records."
            ),
        ) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{class_id}/students/{student_id}",
    response_model=dict[str, int],
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"description": "Class or student not found."},
        409: {"description": "Student is already enrolled in this class."},
    },
)
def enroll_student(
    class_id: int,
    student_id: int,
    session: SessionDependency,
) -> dict[str, int]:
    get_classroom_or_404(class_id, session)
    get_student_or_404(student_id, session)

    enrollment_key = (student_id, class_id)
    if session.get(StudentClassEnrollment, enrollment_key) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Student {student_id} is already enrolled in class {class_id}."
            ),
        )

    session.add(
        StudentClassEnrollment(student_id=student_id, class_id=class_id)
    )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        if session.get(Classroom, class_id) is None:
            get_classroom_or_404(class_id, session)
        if session.get(Student, student_id) is None:
            get_student_or_404(student_id, session)
        if session.get(StudentClassEnrollment, enrollment_key) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Student {student_id} is already enrolled in class {class_id}."
                ),
            ) from None
        raise

    return {"student_id": student_id, "class_id": class_id}


@router.delete(
    "/{class_id}/students/{student_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses={404: {"description": "Class, student, or enrollment not found."}},
)
def unenroll_student(
    class_id: int,
    student_id: int,
    session: SessionDependency,
) -> Response:
    get_classroom_or_404(class_id, session)
    get_student_or_404(student_id, session)

    enrollment = session.get(StudentClassEnrollment, (student_id, class_id))
    if enrollment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Student {student_id} is not enrolled in class {class_id}."
            ),
        )

    session.delete(enrollment)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{class_id}/students",
    response_model=list[StudentRead],
    responses={404: {"description": "Class not found."}},
)
def list_class_students(
    class_id: int,
    session: SessionDependency,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[Student]:
    get_classroom_or_404(class_id, session)
    statement = (
        select(Student)
        .join(
            StudentClassEnrollment,
            StudentClassEnrollment.student_id == Student.id,
        )
        .where(StudentClassEnrollment.class_id == class_id)
        .order_by(Student.id)
        .offset(offset)
        .limit(limit)
    )
    return list(session.exec(statement).all())
