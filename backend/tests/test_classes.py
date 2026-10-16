import pytest
from fastapi.testclient import TestClient


def test_class_crud_and_duplicate_names_are_allowed(client: TestClient) -> None:
    first = client.post(
        "/classes", json={"name": "  Robotics  ", "description": "Build robots"}
    )
    duplicate = client.post("/classes", json={"name": "Robotics"})

    assert first.status_code == 201
    assert first.json() == {
        "id": first.json()["id"],
        "name": "Robotics",
        "description": "Build robots",
    }
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] != first.json()["id"]
    assert duplicate.json()["description"] is None

    listed = client.get("/classes")
    fetched = client.get(f"/classes/{first.json()['id']}")
    updated = client.put(
        f"/classes/{first.json()['id']}",
        json={"name": "  Coding Club ", "description": None},
    )

    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == sorted(
        [first.json()["id"], duplicate.json()["id"]]
    )
    assert fetched.json() == first.json()
    assert updated.status_code == 200
    assert updated.json() == {
        "id": first.json()["id"],
        "name": "Coding Club",
        "description": None,
    }
    deleted = client.delete(f"/classes/{first.json()['id']}")
    assert deleted.status_code == 204
    assert client.get(f"/classes/{first.json()['id']}").status_code == 404


@pytest.mark.parametrize("name", ["", "   "])
def test_create_class_rejects_blank_name(client: TestClient, name: str) -> None:
    response = client.post("/classes", json={"name": name})

    assert response.status_code == 422


def test_update_class_rejects_blank_name(client: TestClient) -> None:
    created = client.post("/classes", json={"name": "Science"})

    response = client.put(
        f"/classes/{created.json()['id']}",
        json={"name": "   ", "description": "Invalid update"},
    )

    assert response.status_code == 422


def test_class_routes_return_404_for_missing_class(client: TestClient) -> None:
    missing_id = 2147483000

    assert client.get(f"/classes/{missing_id}").status_code == 404
    assert client.put(
        f"/classes/{missing_id}", json={"name": "Art", "description": None}
    ).status_code == 404
    assert client.delete(f"/classes/{missing_id}").status_code == 404
    assert client.get(f"/classes/{missing_id}/students").status_code == 404


def test_class_list_uses_bounded_pagination(client: TestClient) -> None:
    created = [
        client.post("/classes", json={"name": name}).json()
        for name in ("Art", "Music", "Robotics")
    ]

    page = client.get("/classes", params={"offset": 1, "limit": 1})

    assert page.status_code == 200
    assert [item["id"] for item in page.json()] == [created[1]["id"]]
    assert client.get("/classes", params={"limit": 0}).status_code == 422
    assert client.get("/classes", params={"limit": 101}).status_code == 422
    assert client.get("/classes", params={"offset": -1}).status_code == 422
    assert client.get("/classes", params={"offset": 10001}).status_code == 422


def test_class_delete_is_allowed_without_enrollments(client: TestClient) -> None:
    created = client.post("/classes", json={"name": "Chess"})

    response = client.delete(f"/classes/{created.json()['id']}")

    assert response.status_code == 204

