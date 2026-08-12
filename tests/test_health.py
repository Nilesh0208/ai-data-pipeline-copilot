"""Tests for application health endpoints and database checks."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.main import app
from database.health import check_database_connection


client = TestClient(app)


def test_root_endpoint_returns_application_information() -> None:
    response = client.get("/")

    assert response.status_code == 200
    payload = response.json()
    assert payload["application"] == "AI Data Pipeline Copilot"
    assert payload["status"] == "running"
    assert payload["phase"] == "Phase 1 - Project Foundation"


def test_health_endpoint_structure(monkeypatch) -> None:
    def mock_check_database_connection() -> dict[str, str]:
        return {"status": "connected", "detail": "Database connection succeeded"}

    monkeypatch.setattr("app.health.check_database_connection", mock_check_database_connection)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "application": "AI Data Pipeline Copilot",
        "database": "connected",
    }


def test_health_endpoint_handles_database_unavailable(monkeypatch) -> None:
    def mock_check_database_connection() -> dict[str, str]:
        return {"status": "disconnected", "detail": "Database connection failed"}

    monkeypatch.setattr("app.health.check_database_connection", mock_check_database_connection)

    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "degraded"
    assert payload["database"] == "disconnected"


def test_database_health_check_connected() -> None:
    engine = MagicMock()
    connection_context = engine.connect.return_value
    connection = connection_context.__enter__.return_value

    result = check_database_connection(engine)

    assert result["status"] == "connected"
    connection.execute.assert_called_once()


def test_database_health_check_failure() -> None:
    engine = MagicMock()
    engine.connect.side_effect = OperationalError("SELECT 1", {}, Exception("failed"))

    result = check_database_connection(engine)

    assert result == {
        "status": "disconnected",
        "detail": "Database connection failed",
    }
