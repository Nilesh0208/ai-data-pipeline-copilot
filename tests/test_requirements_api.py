"""Tests for pipeline requirement API endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from pipeline.examples import customer_revenue_daily_requirement


client = TestClient(app)


def test_requirements_example_endpoint() -> None:
    response = client.get("/requirements/example")

    assert response.status_code == 200
    payload = response.json()
    assert payload["pipeline_name"] == "customer_revenue_daily"
    assert payload["sources"][0]["table"] == {"schema_name": "raw", "table_name": "customers"}
    assert payload["target"]["write_mode"] == "merge"


def test_requirements_validate_endpoint_returns_normalized_requirement() -> None:
    payload = customer_revenue_daily_requirement().model_dump()
    payload["pipeline_name"] = "Customer_Revenue_Daily"

    response = client.post("/requirements/validate", json=payload)

    assert response.status_code == 200
    assert response.json()["pipeline_name"] == "customer_revenue_daily"


def test_requirements_validate_endpoint_handles_invalid_payload() -> None:
    payload = customer_revenue_daily_requirement().model_dump()
    payload["target"]["write_mode"] = "upsert"

    response = client.post("/requirements/validate", json=payload)

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert any(error["loc"][-1] == "write_mode" for error in detail)
