from typing import Literal
from functools import lru_cache
from typing import Generator, Literal

from sqlalchemy import Engine, create_engine
from sqlmodel import Session

from app.core.config import Settings, get_settings


def create_database_engine(
    target: Literal["dev", "test"], settings: Settings | None = None
) -> Engine:
    current_settings = settings or get_settings()
    return create_engine(current_settings.database_url(target), pool_pre_ping=True)


@lru_cache(maxsize=1)
def _development_engine() -> Engine:
    return create_database_engine("dev")


def get_session() -> Generator[Session, None, None]:
    with Session(_development_engine()) as session:
        yield session
