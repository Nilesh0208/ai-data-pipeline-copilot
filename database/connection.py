"""SQLAlchemy database connection utilities."""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import URL, create_engine
from sqlalchemy.engine import Engine

from config.settings import Settings, get_settings


POSTGRES_CONNECT_TIMEOUT_SECONDS = 3


def build_database_url(settings: Settings) -> URL:
    """Build a SQLAlchemy URL without exposing the password in logs."""
    return URL.create(
        drivername="postgresql+psycopg",
        username=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
    )


@lru_cache
def get_engine() -> Engine:
    """Create and cache the SQLAlchemy engine."""
    settings = get_settings()
    return create_engine(
        build_database_url(settings),
        connect_args={"connect_timeout": POSTGRES_CONNECT_TIMEOUT_SECONDS},
        pool_pre_ping=True,
        future=True,
    )
