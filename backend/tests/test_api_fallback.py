import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.main import frontend_route


def test_unknown_session_api_path_is_rejected_before_spa_fallback() -> None:
    path = "/sessions/123/not-a-real-route"
    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": [],
            "server": ("test", 80),
            "client": ("test", 1),
        }
    )

    with pytest.raises(HTTPException) as error:
        frontend_route("sessions/123/not-a-real-route", request)

    assert error.value.status_code == 404
    assert error.value.detail == "Not Found"
