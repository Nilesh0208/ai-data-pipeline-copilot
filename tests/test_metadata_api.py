"""Tests for metadata API endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient

from agent.tools.metadata_tools import (
    InvalidLimitError,
    RowCountResult,
    TableMetadataResult,
    TableReference,
    TableSchemaResult,
)
from app.main import app


client = TestClient(app)


def test_metadata_tables_endpoint(monkeypatch) -> None:
    monkeypatch.setattr("app.metadata.list_tables", lambda: [TableReference(schema_name="raw", table_name="orders")])

    response = client.get("/metadata/tables")

    assert response.status_code == 200
    assert response.json() == [{"schema_name": "raw", "table_name": "orders"}]


def test_metadata_schema_endpoint_returns_404_for_unknown_table(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.metadata.inspect_schema",
        lambda schema_name, table_name: TableSchemaResult(schema_name=schema_name, table_name=table_name, found=False),
    )

    response = client.get("/metadata/schema/raw/missing")

    assert response.status_code == 404


def test_metadata_table_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.metadata.get_table_metadata",
        lambda schema_name, table_name: TableMetadataResult(
            schema_name=schema_name,
            table_name=table_name,
            table_type="source",
            description="Raw orders.",
        ),
    )

    response = client.get("/metadata/table/raw/orders")

    assert response.status_code == 200
    assert response.json()["table_type"] == "source"


def test_metadata_sample_endpoint_rejects_invalid_limit(monkeypatch) -> None:
    def raise_invalid_limit(schema_name: str, table_name: str, limit: int) -> object:
        raise InvalidLimitError("Sample limit must be between 1 and 20")

    monkeypatch.setattr("app.metadata.get_sample_records", raise_invalid_limit)

    response = client.get("/metadata/sample/raw/orders?limit=21")

    assert response.status_code == 400
    assert response.json()["detail"] == "Sample limit must be between 1 and 20"


def test_metadata_count_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.metadata.get_row_count",
        lambda schema_name, table_name: RowCountResult(schema_name=schema_name, table_name=table_name, row_count=35),
    )

    response = client.get("/metadata/count/raw/orders")

    assert response.status_code == 200
    assert response.json()["row_count"] == 35
