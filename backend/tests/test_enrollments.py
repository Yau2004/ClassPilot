import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.session import get_session
from app.main import app
from app.models import StudentClassEnrollment


def create_student_and_class(client: TestClient) -> tuple[int, int]:
    student_response = client.post(
        "/students", json={"name": "Mina Chen", "age": 10}
    )
    class_response = client.post(
        "/classes", json={"name": "Robotics", "description": "Build robots"}
    )
    assert student_response.status_code == 201
    assert class_response.status_code == 201
    return student_response.json()["id"], class_response.json()["id"]


def test_register_student_and_prevent_duplicate_registration(
    client: TestClient,
) -> None:
    student_id, class_id = create_student_and_class(client)

    created = client.post(f"/classes/{class_id}/students/{student_id}")
    duplicate = client.post(f"/classes/{class_id}/students/{student_id}")

    assert created.status_code == 201
    assert created.json() == {"student_id": student_id, "class_id": class_id}
    assert duplicate.status_code == 409


def test_registration_returns_404_for_missing_student_or_class(
    client: TestClient,
) -> None:
    student_id, class_id = create_student_and_class(client)

    assert client.post(f"/classes/{2147483000}/students/{student_id}").status_code == 404
    assert client.post(f"/classes/{class_id}/students/{2147483000}").status_code == 404


def test_class_student_list_is_ordered_and_paginated(client: TestClient) -> None:
    class_id = client.post("/classes", json={"name": "Science"}).json()["id"]
    student_ids = []
    for name, age in (("Ari Lau", 9), ("Ivy Ng", 11), ("Tao Ho", 12)):
        student_id = client.post(
            "/students", json={"name": name, "age": age}
        ).json()["id"]
        student_ids.append(student_id)
        assert client.post(
            f"/classes/{class_id}/students/{student_id}"
        ).status_code == 201

    response = client.get(
        f"/classes/{class_id}/students", params={"offset": 1, "limit": 1}
    )

    assert response.status_code == 200
    assert [student["id"] for student in response.json()] == [student_ids[1]]
    assert client.get(
        f"/classes/{class_id}/students", params={"limit": 101}
    ).status_code == 422
    assert client.get(
        f"/classes/{class_id}/students", params={"offset": 10001}
    ).status_code == 422


def test_unenroll_returns_404_for_missing_resource_or_registration(
    client: TestClient,
) -> None:
    student_id, class_id = create_student_and_class(client)

    assert client.delete(
        f"/classes/{2147483000}/students/{student_id}"
    ).status_code == 404
    assert client.delete(
        f"/classes/{class_id}/students/{2147483000}"
    ).status_code == 404
    assert client.delete(
        f"/classes/{class_id}/students/{student_id}"
    ).status_code == 404


def test_enrolled_student_and_class_cannot_be_deleted_until_unenrolled(
    client: TestClient,
) -> None:
    student_id, class_id = create_student_and_class(client)
    assert client.post(f"/classes/{class_id}/students/{student_id}").status_code == 201

    class_delete = client.delete(f"/classes/{class_id}")
    student_delete = client.delete(f"/students/{student_id}")

    assert class_delete.status_code == 409
    assert student_delete.status_code == 409

    unenrolled = client.delete(f"/classes/{class_id}/students/{student_id}")
    assert unenrolled.status_code == 204
    assert client.delete(f"/classes/{class_id}").status_code == 204
    assert client.delete(f"/students/{student_id}").status_code == 204


def test_enrollment_database_constraints_and_test_database_isolation(
    client: TestClient,
) -> None:
    student_id, class_id = create_student_and_class(client)
    assert client.post(f"/classes/{class_id}/students/{student_id}").status_code == 201

    session_provider = app.dependency_overrides[get_session]
    session_generator = session_provider()
    session = next(session_generator)
    try:
        actual_database = session.connection().execute(
            text("SELECT current_database()")
        ).scalar_one()
        assert actual_database == "steam_classroom_test"

        inspector = inspect(session.connection())
        primary_key = inspector.get_pk_constraint("student_class_enrollments")
        assert primary_key["constrained_columns"] == ["student_id", "class_id"]
        foreign_keys = inspector.get_foreign_keys("student_class_enrollments")
        assert {
            (
                key["constrained_columns"][0],
                key["referred_table"],
                key["options"].get("ondelete"),
            )
            for key in foreign_keys
        } == {
            ("student_id", "students", "RESTRICT"),
            ("class_id", "classes", "RESTRICT"),
        }

        session.add(
            StudentClassEnrollment(student_id=student_id, class_id=class_id)
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
    finally:
        session_generator.close()
