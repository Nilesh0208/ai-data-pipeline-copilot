"""Application entry point for the FastAPI service."""

from __future__ import annotations

import logging

from fastapi import FastAPI

from app.agent import router as agent_router

from app.health import router as health_router
from app.metadata import router as metadata_router
from app.requirements import router as requirements_router
from app.sql import router as sql_router
from config.settings import get_settings


settings = get_settings()

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.app_name,
    description="Project foundation for AI Data Pipeline Copilot.",
    version="0.1.0",
)

app.include_router(health_router)
app.include_router(metadata_router)
app.include_router(requirements_router)
app.include_router(agent_router)
app.include_router(sql_router)


@app.get("/")
def read_root() -> dict[str, str]:
    """Return basic application information."""
    logger.debug("Root endpoint requested")
    return {
        "application": settings.app_name,
        "environment": settings.app_env,
        "status": "running",
        "phase": "Phase 6 - SQL Generation",
    }


