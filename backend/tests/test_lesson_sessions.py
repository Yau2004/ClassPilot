from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as OrmSession

from app.db.session import get_session
from app.main import app
from app.models.lesson_session import SessionParticipant
from app.models.student import Student, SupportNeedStatus


def _student(client: TestClient, name: str, **fields) -> int:
    response = client.post(
        "/students",
        json={"name": name, "gender": "other", "support_need_status": "none_reported", **fields},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _legacy_unreported_student(name: str) -> int:
    """Insert a legacy not_provided row inside the fixture's rollback transaction."""
    dependency = app.dependency_overrides[get_session]
    session_generator = dependency()
    session = next(session_generator)
    student = Student(
        name=name,
        support_need_status=SupportNeedStatus.NOT_PROVIDED,
    )
    session.add(student)
    session.flush()
    student_id = student.id
    session.commit()
    try:
        next(session_generator)
    except StopIteration:
        pass
    assert student_id is not None
    return student_id


def _session(client: TestClient, student_ids: list[int], **fields) -> dict:
    response = client.post(
        "/sessions",
        json={"participant_ids": student_ids, **fields},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _student_profiles(client: TestClient, *, include_archived: bool = False) -> list[dict]:
    profiles: list[dict] = []
    for offset in range(0, 100000, 100):
        response = client.get(
            "/students/profiles",
            params={"include_archived": include_archived, "offset": offset, "limit": 100},
        )
        assert response.status_code == 200, response.text
        page = response.json()
        profiles.extend(page)
        if len(page) < 100:
            return profiles
    raise AssertionError("Student profile pagination did not terminate.")


def test_same_student_same_day_has_independent_session_records(client: TestClient) -> None:
    first_student = _student(
        client,
        "Demo Student One",
        support_need_status="needs_support",
        support_need_tags=["attention_support"],
        support_notes="Demo teaching note",
    )
    second_student = _student(client, "Demo Student Two", age=None)
    student_profile = client.get(f"/students/{first_student}/profile")
    student_summary = client.get(f"/students/{first_student}")
    assert student_profile.status_code == 200
    assert student_profile.json()["support_need_status"] == "needs_support"
    assert student_summary.json() == {
        "id": first_student,
        "name": "Demo Student One",
        "age": None,
    }
    legacy_update = client.put(
        f"/students/{first_student}",
        json={"name": "Demo Student One", "age": None},
    )
    assert legacy_update.status_code == 200
    assert client.get(f"/students/{first_student}/profile").json()["support_need_tags"] == [
        "attention_support"
    ]
    listed = client.get("/students")
    listed_student = next(item for item in listed.json() if item["id"] == first_student)
    assert listed_student["is_archived"] is False
    assert "support_need_tags" not in listed_student


    unassigned = _session(client, [first_student], course_type="Minecraft Education")
    unassigned_rows = client.get("/sessions/unassigned").json()
    assert any(item["id"] == unassigned["id"] for item in unassigned_rows)

    date_only = _session(
        client,
        [first_student],
        course_type="Programming",
        session_date="2026-10-31",
    )
    assert date_only["start_time"] is None and date_only["end_time"] is None
    session_date = "2026-10-31"
    first = _session(
        client,
        [first_student, second_student],
        course_type="Roblox Studio",
        session_date=session_date,
        start_time="09:00",
        end_time="10:00",
        confirmation_status="confirmed",
    )
    second = _session(
        client,
        [first_student],
        course_type="Programming",
        session_date=session_date,
        start_time="11:00",
        end_time="12:00",
    )

    unrecorded = {row["student_id"]: row for row in first["participants"]}
    assert unrecorded[first_student]["attendance_status"] == "unrecorded"
    assert unrecorded[second_student]["attendance_status"] == "unrecorded"
    assert "support_need_tags" in unrecorded[first_student]["student"]

    saved = client.patch(
        f"/sessions/{first['id']}/participant-records",
        json={
            "participants": [
                {"student_id": first_student, "attendance_status": "present", "evaluation": "Demo first review"},
                {"student_id": second_student, "attendance_status": "excused", "evaluation": "Demo second review"},
            ]
        },
    )
    assert saved.status_code == 200, saved.text

    # A partial update changes attendance only; the omitted evaluation is kept.
    partial = client.patch(
        f"/sessions/{first['id']}/participant-records",
        json={"participants": [{"student_id": first_student, "attendance_status": "late"}]},
    )
    assert partial.status_code == 200, partial.text
    first_rows = {row["student_id"]: row for row in partial.json()["participants"]}
    assert first_rows[first_student]["attendance_status"] == "late"
    assert first_rows[first_student]["evaluation"] == "Demo first review"
    assert first_rows[second_student]["attendance_status"] == "excused"
    assert first_rows[second_student]["evaluation"] == "Demo second review"

    second_saved = client.patch(
        f"/sessions/{second['id']}/participant-records",
        json={"participants": [{"student_id": first_student, "attendance_status": "present", "evaluation": "Demo second lesson review"}]},
    )
    assert second_saved.status_code == 200, second_saved.text
    first_after = client.get(f"/sessions/{first['id']}").json()
    second_after = client.get(f"/sessions/{second['id']}").json()
    assert first_after["participants"][0]["evaluation"] == "Demo first review"
    assert second_after["participants"][0]["evaluation"] == "Demo second lesson review"

    student_schedule = client.get(f"/students/{first_student}/sessions")
    assert student_schedule.status_code == 200
    schedule_rows = {item["id"]: item for item in student_schedule.json()}
    assert schedule_rows[first["id"]]["evaluation"] == "Demo first review"
    assert schedule_rows[second["id"]]["evaluation"] == "Demo second lesson review"
    assert schedule_rows[date_only["id"]]["session_date"] == "2026-10-31"
    assert all("support_need_tags" not in item for item in student_schedule.json())

    cleared = client.patch(
        f"/sessions/{first['id']}/participant-records",
        json={"participants": [{"student_id": second_student, "evaluation": None}]},
    )
    cleared_rows = {row["student_id"]: row for row in cleared.json()["participants"]}
    assert cleared_rows[second_student]["evaluation"] is None

    month = client.get("/sessions/month", params={"month": "2026-10"})
    assert month.status_code == 200
    month_json = month.json()
    assert {item["id"] for item in month_json["sessions"]} >= {
        first["id"], second["id"], date_only["id"]
    }
    assert "support_need_tags" not in month.text
    assert "Demo first review" not in month.text
    assert month_json["timezone"] == "Asia/Hong_Kong"



def test_batched_student_profiles_are_paginated_and_keep_summary_private(client: TestClient) -> None:
    active_id = _student(
        client,
        "Synthetic Profile Student",
        age=None,
        gender="prefer_not_to_say",
        support_need_status="needs_support",
        support_need_tags=["short instructions", "visual steps"],
        support_notes="Synthetic support note",
        general_notes="Synthetic learning note",
    )
    no_need_id = _student(
        client,
        "Synthetic No Support Student",
        support_need_status="none_reported",
    )
    not_provided_id = _legacy_unreported_student("Synthetic Unreported Student")
    archived_id = _student(client, "Synthetic Archived Student")
    assert client.post(f"/students/{archived_id}/archive").status_code == 200

    profiles = client.get("/students/profiles?limit=1")
    assert profiles.status_code == 200
    assert len(profiles.json()) == 1
    first = _student_profiles(client)
    archived = _student_profiles(client, include_archived=True)
    summaries = client.get("/students").json()

    profile = next(item for item in first if item["id"] == active_id)
    assert profile["age"] is None
    assert profile["gender"] == "prefer_not_to_say"
    assert profile["support_need_tags"] == ["short instructions", "visual steps"]
    assert profile["support_need_status"] == "needs_support"
    assert profile["support_notes"] == "Synthetic support note"
    assert profile["general_notes"] == "Synthetic learning note"
    by_id = {item["id"]: item for item in first}
    assert by_id[no_need_id]["support_need_status"] == "none_reported"
    assert by_id[not_provided_id]["support_need_status"] == "not_provided"
    assert client.patch(
        f"/students/{active_id}", json={"support_need_status": "none_reported"}
    ).status_code == 200
    assert client.get(f"/students/{active_id}/profile").json()["support_need_tags"] == [
        "short instructions",
        "visual steps",
    ]
    assert archived_id not in {item["id"] for item in first}
    assert archived_id in {item["id"] for item in archived}
    assert all("support_need_status" not in item for item in summaries)

def test_batch_failure_rolls_back_every_participant_update(client: TestClient) -> None:
    first_student = _student(client, "Atomic Demo One")
    second_student = _student(client, "Atomic Demo Two")
    lesson = _session(client, [first_student, second_student], course_type="Programming")
    initial = client.patch(
        f"/sessions/{lesson['id']}/participant-records",
        json={"participants": [
            {"student_id": first_student, "attendance_status": "present", "evaluation": "before-one"},
            {"student_id": second_student, "attendance_status": "excused", "evaluation": "before-two"},
        ]},
    )
    assert initial.status_code == 200

    def fail_test_batch(session: OrmSession, *_args) -> None:
        if any(
            isinstance(item, SessionParticipant)
            and item.evaluation == "force-test-flush-failure"
            for item in session.dirty
        ):
            raise SQLAlchemyError("test-only batch failure")

    event.listen(OrmSession, "before_flush", fail_test_batch)
    try:
        failed = client.patch(
            f"/sessions/{lesson['id']}/participant-records",
            json={"participants": [
                {"student_id": first_student, "attendance_status": "late", "evaluation": "changed-one"},
                {"student_id": second_student, "attendance_status": "absent", "evaluation": "force-test-flush-failure"},
            ]},
        )
    finally:
        event.remove(OrmSession, "before_flush", fail_test_batch)

    assert failed.status_code == 409
    current = client.get(f"/sessions/{lesson['id']}").json()
    rows = {row["student_id"]: row for row in current["participants"]}
    assert rows[first_student]["attendance_status"] == "present"
    assert rows[first_student]["evaluation"] == "before-one"
    assert rows[second_student]["attendance_status"] == "excused"
    assert rows[second_student]["evaluation"] == "before-two"


def test_removing_archived_participant_preserves_history_and_cancel_does_not_clear_it(
    client: TestClient,
) -> None:
    first_student = _student(client, "History Demo One")
    second_student = _student(client, "History Demo Two")
    lesson = _session(
        client,
        [first_student, second_student],
        course_type="LEGO SPIKE",
        session_date="2026-11-01",
        start_time="09:00",
        end_time="10:00",
    )
    recorded = client.patch(
        f"/sessions/{lesson['id']}/participant-records",
        json={"participants": [{"student_id": second_student, "attendance_status": "present", "evaluation": "Demo preserved review"}]},
    )
    assert recorded.status_code == 200

    archived = client.post(f"/students/{second_student}/archive")
    assert archived.status_code == 200
    assert archived.json()["is_archived"] is True
    active_ids = {item["id"] for item in client.get("/students").json()}
    all_ids = {
        item["id"]
        for item in client.get("/students", params={"include_archived": "true"}).json()
    }
    assert second_student not in active_ids
    assert second_student in all_ids
    assert client.delete(f"/students/{second_student}").status_code == 409
    legacy_class = client.post("/classes", json={"name": "Archive Behavior Demo"}).json()
    assert client.post(
        f"/classes/{legacy_class['id']}/students/{second_student}"
    ).status_code == 409
    assert client.post(
        f"/classes/{legacy_class['id']}/attendance",
        json={
            "student_id": second_student,
            "attendance_date": "2026-11-01",
            "status": "present",
        },
    ).status_code == 409

    update = client.patch(f"/sessions/{lesson['id']}", json={"participant_ids": [first_student]})
    assert update.status_code == 200, update.text
    removed = next(row for row in update.json()["participants"] if row["student_id"] == second_student)
    assert removed["removed_at"] is not None
    assert removed["attendance_status"] == "present"
    assert removed["evaluation"] == "Demo preserved review"

    cancelled = client.patch(
        f"/sessions/{lesson['id']}",
        json={"lifecycle_status": "cancelled"},
    )
    assert cancelled.status_code == 200, cancelled.text
    historical = client.get(f"/sessions/{lesson['id']}").json()
    removed_after_cancel = next(row for row in historical["participants"] if row["student_id"] == second_student)
    assert removed_after_cancel["attendance_status"] == "present"
    assert removed_after_cancel["evaluation"] == "Demo preserved review"
    assert client.patch(
        f"/sessions/{lesson['id']}/participant-records",
        json={"participants": [{"student_id": first_student, "attendance_status": "absent"}]},
    ).status_code == 409


def test_overlap_is_reported_but_does_not_reject_schedule(client: TestClient) -> None:
    student_id = _student(client, "Overlap Demo Student")
    _session(
        client,
        [student_id],
        course_type="Roblox Studio",
        session_date="2026-11-15",
        start_time="10:00",
        end_time="11:30",
    )
    overlapping = _session(
        client,
        [student_id],
        course_type="Programming",
        session_date="2026-11-15",
        start_time="11:00",
        end_time="12:00",
    )

    assert overlapping["id"] > 0
    assert len(overlapping["overlap_warnings"]) == 1
