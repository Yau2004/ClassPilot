from pathlib import Path
import hashlib

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes.attendance import router as attendance_router
from app.api.routes.classes import router as classes_router
from app.api.routes.lesson_sessions import router as lesson_sessions_router
from app.api.routes.students import router as students_router
from app.core.config import get_settings
from app.core.runtime import RUNTIME_PATHS

app = FastAPI(title="ClassPilot API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173"],
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type"],
)
app.include_router(students_router)
app.include_router(classes_router)
app.include_router(attendance_router)
app.include_router(lesson_sessions_router)

FRONTEND_DIST = RUNTIME_PATHS.frontend_dist
API_PATHS = ("/students", "/classes", "/sessions")
RESERVED_PATHS = (
    "/health", "/ready", "/docs", "/redoc", "/openapi.json",
)
INSTANCE_ID = hashlib.sha256(
    str(RUNTIME_PATHS.project_root).casefold().encode("utf-8")
).hexdigest()[:16]
REQUIRED_SCHEMA_REVISION = "20261019_0004"


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready", tags=["health"])
def readiness_check() -> dict[str, str]:
    """Read-only startup probe; report no connection details to the caller."""
    try:
        settings = get_settings()
        database_url = settings.database_url("dev")
    except (ValueError, OSError):
        raise HTTPException(
            status_code=503,
            detail="Service is not ready.",
            headers={"X-ClassPilot-Readiness-Reason": "configuration_incomplete"},
        ) from None

    try:
        engine = create_engine(
            database_url,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 3},
        )
    except (SQLAlchemyError, ValueError):
        raise HTTPException(
            status_code=503,
            detail="Service is not ready.",
            headers={"X-ClassPilot-Readiness-Reason": "database_unavailable"},
        ) from None
    try:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError:
            raise HTTPException(
                status_code=503,
                detail="Service is not ready.",
                headers={"X-ClassPilot-Readiness-Reason": "database_unavailable"},
            ) from None

        try:
            with engine.connect() as connection:
                revision = connection.execute(
                    text("SELECT version_num FROM public.alembic_version")
                ).scalar_one_or_none()
        except SQLAlchemyError:
            raise HTTPException(
                status_code=503,
                detail="Service is not ready.",
                headers={"X-ClassPilot-Readiness-Reason": "schema_unavailable"},
            ) from None
    finally:
        engine.dispose()
    if revision != REQUIRED_SCHEMA_REVISION:
        raise HTTPException(
            status_code=503,
            detail="Service is not ready.",
            headers={"X-ClassPilot-Readiness-Reason": "schema_mismatch"},
        )
    return {"status": "ready"}


@app.middleware("http")
async def identify_classpilot(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-ClassPilot-Service"] = "ClassPilot"
    response.headers["X-ClassPilot-Instance"] = INSTANCE_ID
    return response


@app.api_route("/{frontend_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
def frontend_route(frontend_path: str, request: Request):
    """Serve the built UI while keeping unknown API URLs as API 404s."""
    path = request.url.path
    if path in API_PATHS or any(path.startswith(prefix + "/") for prefix in API_PATHS):
        raise HTTPException(status_code=404, detail="Not Found")
    if path in RESERVED_PATHS or any(path.startswith(prefix + "/") for prefix in RESERVED_PATHS):
        raise HTTPException(status_code=404, detail="Not Found")

    root = FRONTEND_DIST.resolve()
    candidate = (root / frontend_path).resolve()
    if root not in candidate.parents and candidate != root:
        raise HTTPException(status_code=404, detail="Not Found")
    if path == "/assets" or path.startswith("/assets/"):
        if candidate.is_file():
            return FileResponse(candidate)
        raise HTTPException(status_code=404, detail="Not Found")
    if candidate.is_file():
        return FileResponse(candidate)
    index = root / "index.html"
    if index.is_file():
        return FileResponse(index)
    raise HTTPException(status_code=404, detail="Frontend build is missing.")
