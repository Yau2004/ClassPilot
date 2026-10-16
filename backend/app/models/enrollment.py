from sqlalchemy import Index, PrimaryKeyConstraint
from sqlmodel import Field, SQLModel


class StudentClassEnrollment(SQLModel, table=True):
    __tablename__ = "student_class_enrollments"
    __table_args__ = (
        PrimaryKeyConstraint(
            "student_id",
            "class_id",
            name="pk_student_class_enrollments",
        ),
        Index("ix_student_class_enrollments_class_id", "class_id"),
    )

    student_id: int = Field(foreign_key="students.id", ondelete="RESTRICT")
    class_id: int = Field(foreign_key="classes.id", ondelete="RESTRICT")
