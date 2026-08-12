"""Health-check API routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter

from config.settings import get_settings
from database.health import check_database_connection


logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health")
def read_health() -> dict[str, str]:
    """Return application and database health without crashing on DB failures."""
    settings = get_settings()
    database_health = check_database_connection()
    database_status = database_health["status"]
    application_status = "healthy" if database_status == "connected" else "degraded"

    if application_status != "healthy":
        logger.warning("Health check degraded: database status is %s", database_status)

    return {
        "status": application_status,
        "application": settings.app_name,
        "database": database_status,
    }
