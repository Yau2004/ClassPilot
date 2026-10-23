from pathlib import Path

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
import pytest

from app import main
from app.main import app


class FakeRevisionResult:
    def __init__(self, revision: str | None) -> None:
        self.revision = revision

    def scalar_one_or_none(self) -> str | None:
        return self.revision


def test_health_endpoint() -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_frontend_cors_only_allows_local_vite_origin() -> None:
    client = TestClient(app)
    allowed = client.options(
        "/students",
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "POST",
        },
    )
    denied = client.options(
        "/students",
        headers={
            "Origin": "https://example.invalid",
            "Access-Control-Request-Method": "POST",
        },
    )
    patch_allowed = client.options(
        "/sessions/1",
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "PATCH",
        },
    )

    assert allowed.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
    assert "access-control-allow-origin" not in denied.headers
    assert patch_allowed.headers["access-control-allow-methods"].find("PATCH") >= 0


def test_readiness_returns_ready_when_database_probe_succeeds(monkeypatch) -> None:
    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, statement):
            if "alembic_version" in str(statement):
                return FakeRevisionResult(main.REQUIRED_SCHEMA_REVISION)
            return FakeRevisionResult(None)

    class FakeEngine:
        def connect(self):
            return FakeConnection()

        def dispose(self):
            return None

    class FakeSettings:
        def database_url(self, _target):
            return "postgresql+psycopg://unused/unused"

    monkeypatch.setattr(main, "get_settings", FakeSettings)
    monkeypatch.setattr(main, "create_engine", lambda *_args, **_kwargs: FakeEngine())
    response = TestClient(app).get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert response.headers["x-classpilot-service"] == "ClassPilot"


def test_readiness_refuses_an_old_schema_revision(monkeypatch) -> None:
    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, statement):
            if "alembic_version" in str(statement):
                return FakeRevisionResult("20261015_0003")
            return FakeRevisionResult(None)

    class FakeEngine:
        def connect(self):
            return FakeConnection()

        def dispose(self):
            return None

    class FakeSettings:
        def database_url(self, _target):
            return "postgresql+psycopg://unused/unused"

    monkeypatch.setattr(main, "get_settings", FakeSettings)
    monkeypatch.setattr(main, "create_engine", lambda *_args, **_kwargs: FakeEngine())

    with pytest.raises(HTTPException) as error:
        main.readiness_check()

    assert error.value.status_code == 503
    assert error.value.detail == "Service is not ready."
    assert error.value.headers["X-ClassPilot-Readiness-Reason"] == "schema_mismatch"


def test_readiness_uses_only_read_queries_for_current_schema(monkeypatch) -> None:
    statements: list[str] = []

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, statement):
            sql = str(statement)
            statements.append(sql)
            revision = main.REQUIRED_SCHEMA_REVISION if "alembic_version" in sql else None
            return FakeRevisionResult(revision)

    class FakeEngine:
        def connect(self):
            return FakeConnection()

        def dispose(self):
            return None

    class FakeSettings:
        def database_url(self, _target):
            return "postgresql+psycopg://unused/unused"

    monkeypatch.setattr(main, "get_settings", FakeSettings)
    monkeypatch.setattr(main, "create_engine", lambda *_args, **_kwargs: FakeEngine())

    assert main.readiness_check() == {"status": "ready"}
    assert statements == [
        "SELECT 1",
        "SELECT version_num FROM public.alembic_version",
    ]


def test_readiness_returns_generic_error_when_database_probe_fails(monkeypatch) -> None:
    class FakeEngine:
        def connect(self):
            raise OperationalError("connect", {}, RuntimeError("private detail"))

        def dispose(self):
            return None

    class FakeSettings:
        def database_url(self, _target):
            return "postgresql+psycopg://unused/unused"

    monkeypatch.setattr(main, "get_settings", FakeSettings)
    monkeypatch.setattr(main, "create_engine", lambda *_args, **_kwargs: FakeEngine())
    response = TestClient(app).get("/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "Service is not ready."}
    assert response.headers["x-classpilot-readiness-reason"] == "database_unavailable"
    assert "private detail" not in response.text


def test_spa_fallback_serves_build_and_keeps_api_404(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<main>ClassPilot</main>", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("export {}", encoding="utf-8")
    monkeypatch.setattr(main, "FRONTEND_DIST", tmp_path)
    client = TestClient(app)

    page = client.get("/calendar/2026/10")
    api_not_found = client.get("/classes/1/not-a-real-route")
    session_api_error = client.get("/sessions/123/not-a-real-route")
    missing_asset = client.get("/assets/no-file.js")
    asset = client.get("/assets/app.js")

    assert page.status_code == 200
    assert page.text == "<main>ClassPilot</main>"
    assert api_not_found.status_code == 404
    assert api_not_found.json() == {"detail": "Not Found"}
    assert session_api_error.status_code == 404
    assert session_api_error.headers["content-type"].startswith("application/json")
    assert "ClassPilot</main>" not in session_api_error.text
    assert missing_asset.status_code == 404
    assert asset.status_code == 200
    assert asset.text == "export {}"
