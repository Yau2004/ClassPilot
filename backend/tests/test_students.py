from fastapi.testclient import TestClient
import pytest


def _create_student(client: TestClient, payload: dict[str, object]):
    return client.post(
        "/students",
        json={"gender": "other", "support_need_status": "none_reported", **payload},
    )


def test_create_student_returns_public_fields(client: TestClient) -> None:
    response = _create_student(client, {"name": "Mina Chen", "age": 10})

    assert response.status_code == 201
    assert set(response.json()) == {"id", "name", "age"}
    assert response.json()["id"] > 0
    assert response.json()["name"] == "Mina Chen"
    assert response.json()["age"] == 10


def test_create_student_trims_name(client: TestClient) -> None:
    response = _create_student(client, {"name": "  Kai Wong  ", "age": 12})

    assert response.status_code == 201
    assert response.json()["name"] == "Kai Wong"


@pytest.mark.parametrize("name", ["", "   ", "S" * 101])
def test_create_student_rejects_invalid_name(
    client: TestClient, name: str
) -> None:
    response = _create_student(client, {"name": name, "age": 10})

    assert response.status_code == 422


@pytest.mark.parametrize("age", [3, 19])
def test_create_student_rejects_age_out_of_range(
    client: TestClient, age: int
) -> None:
    response = _create_student(client, {"name": "Ari Lau", "age": age})

    assert response.status_code == 422


def test_list_students_returns_id_ordered_results(client: TestClient) -> None:
    first = _create_student(client, {"name": "Tao Ho", "age": 8})
    second = _create_student(client, {"name": "Ivy Ng", "age": 11})

    response = client.get("/students")

    assert response.status_code == 200
    students = response.json()
    assert [student["id"] for student in students] == sorted(
        [first.json()["id"], second.json()["id"]]
    )
    assert [student["name"] for student in students] == ["Tao Ho", "Ivy Ng"]


def test_list_students_returns_empty_list_when_none_exist(
    client: TestClient,
) -> None:
    response = client.get("/students")

    assert response.status_code == 200
    assert response.json() == []


def test_get_existing_student(client: TestClient) -> None:
    created = _create_student(client, {"name": "Nora Yip", "age": 14})

    response = client.get(f"/students/{created.json()['id']}")

    assert response.status_code == 200
    assert response.json() == created.json()


def test_get_missing_student_returns_clear_404(client: TestClient) -> None:
    response = client.get("/students/2147483000")

    assert response.status_code == 404
    assert response.json() == {
        "detail": "Student with id 2147483000 was not found."
    }


def test_update_student_returns_updated_public_fields(client: TestClient) -> None:
    created = _create_student(client, {"name": "Mina Chen", "age": 10})
    student_id = created.json()["id"]

    response = client.put(
        f"/students/{student_id}",
        json={"name": "  Mina Wong  ", "age": 11},
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": student_id,
        "name": "Mina Wong",
        "age": 11,
    }
    assert client.get(f"/students/{student_id}").json() == response.json()


def test_update_missing_student_returns_clear_404(client: TestClient) -> None:
    response = client.put(
        "/students/2147483000", json={"name": "Ari Lau", "age": 9}
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "Student with id 2147483000 was not found."
    }


@pytest.mark.parametrize(
    "student_data",
    [
        {"name": "", "age": 10},
        {"name": "   ", "age": 10},
        {"name": "S" * 101, "age": 10},
        {"name": "Ari Lau", "age": 3},
        {"name": "Ari Lau", "age": 19},
    ],
)
def test_update_student_rejects_invalid_input(
    client: TestClient, student_data: dict[str, object]
) -> None:
    created = _create_student(client, {"name": "Mina Chen", "age": 10})

    response = client.put(f"/students/{created.json()['id']}", json=student_data)

    assert response.status_code == 422


def test_delete_student_returns_no_content_and_removes_student(
    client: TestClient,
) -> None:
    created = _create_student(client, {"name": "Ivy Ng", "age": 11})
    student_id = created.json()["id"]

    response = client.delete(f"/students/{student_id}")

    assert response.status_code == 204
    assert response.content == b""
    missing_response = client.get(f"/students/{student_id}")
    assert missing_response.status_code == 404


def test_delete_missing_student_returns_clear_404(client: TestClient) -> None:
    response = client.delete("/students/2147483000")

    assert response.status_code == 404
    assert response.json() == {
        "detail": "Student with id 2147483000 was not found."
    }
