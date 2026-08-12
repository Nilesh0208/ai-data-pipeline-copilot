"""Tests for Phase 6 SQL generation without live Gemini calls or SQL execution."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from google.genai import errors, types

from app.main import app
from pipeline.examples import customer_revenue_daily_requirement
from sql_generation.generator import SQLStructuredOutputError, generate_sql
from sql_generation.models import GeneratedSQL, SQLValidationStatus
from sql_generation.validator import validate_generated_sql


class FakeModels:
    def __init__(self, responses: list[object] | None = None, error: Exception | None = None) -> None:
        self._responses = responses or []
        self.error = error
        self.calls: list[dict[str, object]] = []

    def generate_content(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        assert self._responses, "Unexpected Gemini SQL generation call"
        return self._responses.pop(0)


class FakeClient:
    def __init__(self, responses: list[object] | None = None, error: Exception | None = None) -> None:
        self.models = FakeModels(responses, error)


def valid_sql_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "pipeline_name": "customer_revenue_daily",
        "dialect": "postgresql",
        "sql": """
MERGE INTO curated.customer_revenue AS target
USING (
    SELECT c.customer_id, c.customer_name, COUNT(o.order_id) AS total_orders,
           SUM(o.amount) AS total_revenue, MAX(o.order_date) AS last_order_date,
           CURRENT_TIMESTAMP AS updated_at
    FROM raw.customers AS c
    JOIN raw.orders AS o ON c.customer_id = o.customer_id
    WHERE o.status = 'COMPLETED'
    GROUP BY c.customer_id, c.customer_name
) AS source
ON target.customer_id = source.customer_id
WHEN MATCHED THEN UPDATE SET
    customer_name = source.customer_name,
    total_orders = source.total_orders,
    total_revenue = source.total_revenue,
    last_order_date = source.last_order_date,
    updated_at = source.updated_at
WHEN NOT MATCHED THEN INSERT (
    customer_id, customer_name, total_orders, total_revenue, last_order_date, updated_at
) VALUES (
    source.customer_id, source.customer_name, source.total_orders, source.total_revenue, source.last_order_date, source.updated_at
);
""".strip(),
        "source_tables": ["raw.customers", "raw.orders"],
        "target_table": "curated.customer_revenue",
        "statement_type": "merge",
        "validation_status": "valid",
        "warnings": [],
        "validation_errors": [],
    }
    payload.update(overrides)
    return payload


def fake_response(payload: dict[str, object]) -> object:
    return SimpleNamespace(text=json.dumps(payload), parsed=None)


def test_valid_sql_generation_from_requirement() -> None:
    requirement = customer_revenue_daily_requirement()
    client = FakeClient([fake_response(valid_sql_payload())])

    result = generate_sql(requirement, client=client)

    assert result.validation_status == SQLValidationStatus.VALID
    assert result.pipeline_name == requirement.pipeline_name
    assert result.statement_type == "merge"
    assert "MERGE INTO curated.customer_revenue" in result.sql
    assert client.models.calls[0]["model"]
    assert isinstance(client.models.calls[0]["config"], types.GenerateContentConfig)


def test_postgresql_dialect_enforcement() -> None:
    requirement = customer_revenue_daily_requirement()
    result = validate_generated_sql(requirement, GeneratedSQL.model_validate(valid_sql_payload(dialect="postgresql")))

    assert result.dialect == "postgresql"
    assert result.validation_status == "valid"


def test_source_table_preservation() -> None:
    requirement = customer_revenue_daily_requirement()
    result = generate_sql(requirement, client=FakeClient([fake_response(valid_sql_payload())]))

    assert result.source_tables == ["raw.customers", "raw.orders"]


def test_target_table_preservation() -> None:
    requirement = customer_revenue_daily_requirement()
    result = generate_sql(requirement, client=FakeClient([fake_response(valid_sql_payload())]))

    assert result.target_table == "curated.customer_revenue"


def test_prohibited_drop_rejection() -> None:
    requirement = customer_revenue_daily_requirement()
    payload = valid_sql_payload(sql="DROP TABLE raw.orders;")

    result = generate_sql(requirement, client=FakeClient([fake_response(payload)]))

    assert result.validation_status == "invalid"
    assert any("prohibited SQL construct" in error for error in result.validation_errors)


def test_prohibited_alter_truncate_rejection() -> None:
    requirement = customer_revenue_daily_requirement()
    payload = valid_sql_payload(sql="ALTER TABLE curated.customer_revenue ADD COLUMN x INT; TRUNCATE raw.orders;")

    result = generate_sql(requirement, client=FakeClient([fake_response(payload)]))

    assert result.validation_status == "invalid"
    assert any("prohibited SQL construct" in error for error in result.validation_errors)
    assert any("one statement" in error for error in result.validation_errors)


def test_unrelated_source_table_rejection() -> None:
    requirement = customer_revenue_daily_requirement()
    sql = valid_sql_payload()["sql"].replace("raw.orders", "raw.payments")
    payload = valid_sql_payload(sql=sql, source_tables=["raw.customers", "raw.payments"])

    result = generate_sql(requirement, client=FakeClient([fake_response(payload)]))

    assert result.validation_status == "invalid"
    assert any("source_tables do not match" in error for error in result.validation_errors)
    assert any("unrelated tables" in error for error in result.validation_errors)


def test_incorrect_target_table_rejection() -> None:
    requirement = customer_revenue_daily_requirement()
    sql = valid_sql_payload()["sql"].replace("curated.customer_revenue", "curated.other_revenue")
    payload = valid_sql_payload(sql=sql, target_table="curated.other_revenue")

    result = generate_sql(requirement, client=FakeClient([fake_response(payload)]))

    assert result.validation_status == "invalid"
    assert any("target_table does not match" in error for error in result.validation_errors)
    assert any("unrelated tables" in error for error in result.validation_errors)


def test_empty_sql_rejection() -> None:
    requirement = customer_revenue_daily_requirement()
    payload = valid_sql_payload(sql=" ")

    with pytest.raises(SQLStructuredOutputError, match="invalid GeneratedSQL"):
        generate_sql(requirement, client=FakeClient([fake_response(payload)]))


def test_controlled_gemini_client_failure() -> None:
    requirement = customer_revenue_daily_requirement()
    error = errors.ClientError(429, {"error": {"message": "quota exceeded"}})

    with pytest.raises(Exception, match="Gemini SQL generation failed"):
        generate_sql(requirement, client=FakeClient(error=error))


def test_api_success_using_mocked_gemini(monkeypatch) -> None:
    expected = GeneratedSQL.model_validate(valid_sql_payload())

    monkeypatch.setattr("app.sql.generate_sql", lambda requirement: expected)
    client = TestClient(app)

    response = client.post("/sql/generate", json=customer_revenue_daily_requirement().model_dump(mode="json"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["pipeline_name"] == "customer_revenue_daily"
    assert payload["validation_status"] == "valid"


def test_api_controlled_failure(monkeypatch) -> None:
    from sql_generation.generator import SQLGenerationError

    def fail(requirement: object) -> None:
        raise SQLGenerationError("Gemini SQL generation failed")

    monkeypatch.setattr("app.sql.generate_sql", fail)
    client = TestClient(app)

    response = client.post("/sql/generate", json=customer_revenue_daily_requirement().model_dump(mode="json"))

    assert response.status_code == 503
    assert response.json()["detail"] == "Gemini SQL generation failed"


def test_sql_generation_does_not_execute_sql(monkeypatch) -> None:
    def fail_if_database_engine_requested() -> None:
        raise AssertionError("SQL generation must not request a database engine")

    monkeypatch.setattr("database.connection.get_engine", fail_if_database_engine_requested)

    result = generate_sql(customer_revenue_daily_requirement(), client=FakeClient([fake_response(valid_sql_payload())]))

    assert result.validation_status == "valid"