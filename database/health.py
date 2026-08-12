"""Database health checks."""

from __future__ import annotations

import logging
from typing import TypedDict

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from database.connection import get_engine


logger = logging.getLogger(__name__)


class DatabaseHealth(TypedDict):
    """Structured database health-check result."""

    status: str
    detail: str


def check_database_connection(engine: Engine | None = None) -> DatabaseHealth:
    """Execute a lightweight database query and return a structured result."""
    database_engine = engine or get_engine()

    try:
        with database_engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "connected", "detail": "Database connection succeeded"}
    except SQLAlchemyError as exc:
        logger.warning("Database health check failed: %s", exc.__class__.__name__)
        return {"status": "disconnected", "detail": "Database connection failed"}
    except Exception as exc:  # Defensive guard so API health never crashes.
        logger.warning("Unexpected database health check failure: %s", exc.__class__.__name__)
        return {"status": "disconnected", "detail": "Database connection failed"}
