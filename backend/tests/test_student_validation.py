import pytest
from pydantic import ValidationError

from app.models.student import (
    StudentCreate,
    StudentGender,
    StudentProfileRead,
    StudentSupportNeedChoice,
    StudentUpdate,
    SupportNeedStatus,
)


def test_create_api_model_requires_gender_and_only_explicit_support_choices() -> None:
    schema = StudentCreate.model_json_schema()

    assert "gender" in schema["required"]
    assert "support_need_status" in schema["required"]
    assert set(StudentGender) == {
        StudentGender.FEMALE,
        StudentGender.MALE,
        StudentGender.OTHER,
        StudentGender.PREFER_NOT_TO_SAY,
    }
    assert set(StudentSupportNeedChoice) == {
        StudentSupportNeedChoice.NONE_REPORTED,
        StudentSupportNeedChoice.NEEDS_SUPPORT,
    }


def test_partial_update_model_keeps_omitted_legacy_fields_distinct_from_null() -> None:
    omitted = StudentUpdate(name="Updated name")

    assert omitted.model_dump(exclude_unset=True) == {"name": "Updated name"}
    legacy_profile = StudentProfileRead(
        id=1,
        name="Legacy Student",
        age=None,
        gender=None,
        support_need_status=SupportNeedStatus.NOT_PROVIDED,
        support_need_tags=[],
        support_notes=None,
        general_notes=None,
        is_archived=False,
    )
    assert legacy_profile.gender is None
    assert legacy_profile.support_need_status is SupportNeedStatus.NOT_PROVIDED


def test_create_and_explicit_updates_reject_unprovided_or_null_choices() -> None:
    with pytest.raises(ValidationError):
        StudentCreate(name="New Student")
    with pytest.raises(ValidationError):
        StudentCreate(
            name="New Student",
            gender="female",
            support_need_status="not_provided",
        )
    with pytest.raises(ValidationError):
        StudentUpdate(gender=None)
    with pytest.raises(ValidationError):
        StudentUpdate(support_need_status="not_provided")
