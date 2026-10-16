from fastapi.testclient import TestClient
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.session import get_session
from app.main import app


def create_enrolled_student(client: TestClient) -> tuple[int, int]:
    student = client.post("/students", json={"name": "Mina Chen", "age": 10})
    classroom = client.post(
        "/classes", json={"name": "Robotics", "description": "Build robots"}
    )
    assert student.status_code == 201
    assert classroom.status_code == 201
    student_id = student.json()["id"]
    class_id = classroom.json()["id"]
    assert client.post(
        f"/classes/{class_id}/students/{student_id}"
    ).status_code == 201
    return student_id, class_id


def create_attendance(
    client: TestClient,
    class_id: int,
    student_id: int,
    attendance_date: str = "2026-09-15",
    status: str = "present",
    note: str | None = None,
):
    return client.post(
        f"/classes/{class_id}/attendance",
        json={
            "student_id": student_id,
            "attendance_date": attendance_date,
            "status": status,
            "note": note,
        },
    )


def test_attendance_create_list_update_and_delete(client: TestClient) -> None:
    student_id, class_id = create_enrolled_student(client)

    created = create_attendance(
        client, class_id, student_id, note="Arrived before class"
    )
    assert created.status_code == 201
    attendance = created.json()
    assert attendance == {
        "id": attendance["id"],
        "class_id": class_id,
        "student_id": student_id,
        "attendance_date": "2026-09-15",
        "status": "present",
        "note": "Arrived before class",
    }

    listed = client.get(f"/classes/{class_id}/attendance")
    updated = client.put(
        f"/classes/{class_id}/attendance/{attendance['id']}",
        json={"status": "late", "note": "Bus delay"},
    )
    assert listed.status_code == 200
    assert listed.json() == [attendance]
    assert updated.status_code == 200
    assert updated.json() == {
        **attendance,
        "status": "late",
        "note": "Bus delay",
    }

    deleted = client.delete(
        f"/classes/{class_id}/attendance/{attendance['id']}"
    )
    assert deleted.status_code == 204
    assert client.get(f"/classes/{class_id}/attendance").json() == []


def test_attendance_rejects_duplicate_but_allows_another_date_or_class(
    client: TestClient,
) -> None:
    student_id, class_id = create_enrolled_student(client)
    duplicate = create_attendance(client, class_id, student_id)
    repeated = create_attendance(client, class_id, student_id)

    another_date = create_attendance(
        client, class_id, student_id, attendance_date="2026-09-16"
    )
    other_class = client.post("/classes", json={"name": "Art"}).json()
    assert client.post(
        f"/classes/{other_class['id']}/students/{student_id}"
    ).status_code == 201
    same_date_other_class = create_attendance(
        client, other_class["id"], student_id
    )

    assert duplicate.status_code == 201
    assert repeated.status_code == 409
    assert another_date.status_code == 201
    assert same_date_other_class.status_code == 201


def test_attendance_requires_existing_enrolled_student_and_class(
    client: TestClient,
) -> None:
    student_id, class_id = create_enrolled_student(client)
    other_student = client.post(
        "/students", json={"name": "Ari Lau", "age": 9}
    ).json()

    unregistered = create_attendance(client, class_id, other_student["id"])
    missing_class = create_attendance(client, 2147483000, student_id)
    missing_student = create_attendance(client, class_id, 2147483000)

    assert unregistered.status_code == 409
    assert missing_class.status_code == 404
    assert missing_student.status_code == 404


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "unknown"},
        {"attendance_date": "not-a-date"},
        {"attendance_date": "2026-02-30"},
        {"student_id": 0},
    ],
)
def test_attendance_create_rejects_invalid_input(
    client: TestClient, overrides: dict[str, object]
) -> None:
    student_id, class_id = create_enrolled_student(client)
    payload: dict[str, object] = {
        "student_id": student_id,
        "attendance_date": "2026-09-15",
        "status": "present",
        "note": None,
    }
    payload.update(overrides)

    response = client.post(f"/classes/{class_id}/attendance", json=payload)

    assert response.status_code == 422


def test_attendance_list_filters_by_date_and_student_with_stable_paging(
    client: TestClient,
) -> None:
    first_student, class_id = create_enrolled_student(client)
    second = client.post("/students", json={"name": "Ivy Ng", "age": 11}).json()
    assert client.post(
        f"/classes/{class_id}/students/{second['id']}"
    ).status_code == 201

    first_day = create_attendance(
        client, class_id, first_student, attendance_date="2026-09-15"
    )
    second_day = create_attendance(
        client, class_id, first_student, attendance_date="2026-09-16"
    )
    same_day_other_student = create_attendance(
        client, class_id, second["id"], attendance_date="2026-09-15"
    )

    by_date = client.get(
        f"/classes/{class_id}/attendance",
        params={"attendance_date": "2026-09-15"},
    )
    by_student = client.get(
        f"/classes/{class_id}/attendance",
        params={"student_id": first_student},
    )
    page = client.get(
        f"/classes/{class_id}/attendance",
        params={"offset": 1, "limit": 1},
    )

    assert first_day.status_code == 201
    assert second_day.status_code == 201
    assert same_day_other_student.status_code == 201
    assert [item["id"] for item in by_date.json()] == [
        first_day.json()["id"],
        same_day_other_student.json()["id"],
    ]
    assert [item["id"] for item in by_student.json()] == [
        first_day.json()["id"],
        second_day.json()["id"],
    ]
    assert [item["id"] for item in page.json()] == [
        same_day_other_student.json()["id"]
    ]
    assert client.get(
        f"/classes/{class_id}/attendance",
        params={"attendance_date": "bad-date"},
    ).status_code == 422
    assert client.get(
        f"/classes/{class_id}/attendance", params={"student_id": 0}
    ).status_code == 422
    assert client.get(
        f"/classes/{class_id}/attendance", params={"limit": 101}
    ).status_code == 422
    assert client.get("/classes/2147483000/attendance").status_code == 404


def test_attendance_update_rejects_invalid_status_and_immutable_fields(
    client: TestClient,
) -> None:
    student_id, class_id = create_enrolled_student(client)
    record = create_attendance(client, class_id, student_id).json()

    invalid_status = client.put(
        f"/classes/{class_id}/attendance/{record['id']}",
        json={"status": "unknown", "note": "Invalid"},
    )
    immutable_field = client.put(
        f"/classes/{class_id}/attendance/{record['id']}",
        json={
            "status": "absent",
            "note": "Changed",
            "student_id": 999,
        },
    )

    assert invalid_status.status_code == 422
    assert immutable_field.status_code == 422


def test_attendance_cannot_be_accessed_through_another_class(
    client: TestClient,
) -> None:
    student_id, class_id = create_enrolled_student(client)
    record = create_attendance(client, class_id, student_id).json()
    other_class_id = client.post("/classes", json={"name": "Art"}).json()["id"]

    update = client.put(
        f"/classes/{other_class_id}/attendance/{record['id']}",
        json={"status": "absent", "note": None},
    )
    delete = client.delete(
        f"/classes/{other_class_id}/attendance/{record['id']}"
    )

    assert update.status_code == 404
    assert delete.status_code == 404
    assert client.get(f"/classes/{class_id}/attendance").json() == [record]


def test_missing_attendance_and_class_return_404(client: TestClient) -> None:
    student_id, class_id = create_enrolled_student(client)
    assert student_id > 0

    assert client.put(
        f"/classes/{class_id}/attendance/2147483000",
        json={"status": "absent", "note": None},
    ).status_code == 404
    assert client.delete(
        f"/classes/{class_id}/attendance/2147483000"
    ).status_code == 404
    assert client.put(
        "/classes/2147483000/attendance/2147483000",
        json={"status": "absent", "note": None},
    ).status_code == 404


def test_cancelling_enrollment_preserves_editable_attendance_history(
    client: TestClient,
) -> None:
    student_id, class_id = create_enrolled_student(client)
    record = create_attendance(client, class_id, student_id).json()

    unenrolled = client.delete(
        f"/classes/{class_id}/students/{student_id}"
    )
    history = client.get(f"/classes/{class_id}/attendance")
    updated = client.put(
        f"/classes/{class_id}/attendance/{record['id']}",
        json={"status": "excused", "note": "Family appointment"},
    )

    assert unenrolled.status_code == 204
    assert history.status_code == 200
    assert history.json() == [record]
    assert updated.status_code == 200
    assert updated.json()["status"] == "excused"
    assert updated.json()["student_id"] == student_id
    assert updated.json()["class_id"] == class_id


def test_attendance_blocks_parent_deletion_until_record_is_removed(
    client: TestClient,
) -> None:
    student_id, class_id = create_enrolled_student(client)
    record = create_attendance(client, class_id, student_id).json()

    assert client.delete(f"/classes/{class_id}").status_code == 409
    assert client.delete(f"/students/{student_id}").status_code == 409
    assert client.delete(
        f"/classes/{class_id}/students/{student_id}"
    ).status_code == 204
    assert client.delete(f"/classes/{class_id}").status_code == 409
    assert client.delete(f"/students/{student_id}").status_code == 409

    assert client.delete(
        f"/classes/{class_id}/attendance/{record['id']}"
    ).status_code == 204
    assert client.delete(f"/classes/{class_id}").status_code == 204
    assert client.delete(f"/students/{student_id}").status_code == 204


def test_attendance_database_constraints_and_test_database_isolation(
    client: TestClient,
) -> None:
    student_id, class_id = create_enrolled_student(client)
    record = create_attendance(client, class_id, student_id).json()
    assert client.delete(
        f"/classes/{class_id}/students/{student_id}"
    ).status_code == 204

    session_provider = app.dependency_overrides[get_session]
    session_generator = session_provider()
    session = next(session_generator)
    try:
        connection = session.connection()
        actual_database = connection.execute(
            text("SELECT current_database()")
        ).scalar_one()
        assert actual_database == "steam_classroom_test"

        inspector = inspect(connection)
        unique_constraints = inspector.get_unique_constraints("attendance_records")
        assert any(
            constraint["column_names"]
            == ["class_id", "student_id", "attendance_date"]
            for constraint in unique_constraints
        )
        check_constraints = inspector.get_check_constraints("attendance_records")
        assert any(
            constraint["name"] == "ck_attendance_status"
            for constraint in check_constraints
        )
        foreign_keys = inspector.get_foreign_keys("attendance_records")
        assert {
            (
                key["constrained_columns"][0],
                key["referred_table"],
                key["options"].get("ondelete"),
            )
            for key in foreign_keys
        } == {
            ("class_id", "classes", "RESTRICT"),
            ("student_id", "students", "RESTRICT"),
        }

        duplicate_statement = text(
            "INSERT INTO attendance_records "
            "(class_id, student_id, attendance_date, status, note) "
            "VALUES (:class_id, :student_id, :attendance_date, 'present', NULL)"
        )
        with pytest.raises(IntegrityError):
            session.execute(
                duplicate_statement,
                {
                    "class_id": class_id,
                    "student_id": student_id,
                    "attendance_date": record["attendance_date"],
                },
            )
        session.rollback()

        invalid_status_statement = text(
            "INSERT INTO attendance_records "
            "(class_id, student_id, attendance_date, status, note) "
            "VALUES (:class_id, :student_id, '2026-09-16', 'unknown', NULL)"
        )
        with pytest.raises(IntegrityError):
            session.execute(
                invalid_status_statement,
                {"class_id": class_id, "student_id": student_id},
            )
        session.rollback()

        with pytest.raises(IntegrityError):
            session.execute(
                text("DELETE FROM classes WHERE id = :class_id"),
                {"class_id": class_id},
            )
        session.rollback()
        with pytest.raises(IntegrityError):
            session.execute(
                text("DELETE FROM students WHERE id = :student_id"),
                {"student_id": student_id},
            )
        session.rollback()
    finally:
        session_generator.close()

