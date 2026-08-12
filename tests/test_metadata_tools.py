"""Tests for deterministic metadata intelligence tools."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from sqlalchemy import BigInteger, Column, MetaData, Numeric, Table
from sqlalchemy.exc import OperationalError

from agent.tools import metadata_tools
from agent.tools.metadata_tools import (
    InvalidIdentifierError,
    InvalidLimitError,
    MetadataDatabaseError,
    get_column_metadata,
    get_pipeline_metadata,
    get_row_count,
    get_sample_records,
    get_table_metadata,
    inspect_schema,
    list_tables,
)


class FakeInspector:
    def __init__(self) -> None:
        self.schemas = {"raw", "curated"}
        self.tables = {
            "raw": ["customers", "orders"],
            "curated": ["customer_revenue"],
        }

    def has_schema(self, schema_name: str) -> bool:
        return schema_name in self.schemas

    def get_table_names(self, schema: str) -> list[str]:
        return self.tables.get(schema, [])

    def has_table(self, table_name: str, schema: str) -> bool:
        return table_name in self.tables.get(schema, [])

    def get_pk_constraint(self, table_name: str, schema: str) -> dict[str, list[str]]:
        return {"constrained_columns": ["order_id"]}

    def get_columns(self, table_name: str, schema: str) -> list[dict[str, object]]:
        return [
            {"name": "order_id", "type": BigInteger(), "nullable": False},
            {"name": "amount", "type": Numeric(12, 2), "nullable": False},
        ]


class FakeResult:
    def __init__(self, rows: list[dict[str, object]] | None = None, scalar: int | None = None) -> None:
        self.rows = rows or []
        self.scalar = scalar

    def mappings(self) -> "FakeResult":
        return self

    def first(self) -> dict[str, object] | None:
        return self.rows[0] if self.rows else None

    def all(self) -> list[dict[str, object]]:
        return self.rows

    def scalar_one(self) -> int:
        assert self.scalar is not None
        return self.scalar


class FakeConnection:
    def __init__(self, result: FakeResult) -> None:
        self.result = result
        self.executed: list[object] = []

    def execute(self, query: object, params: dict[str, object] | None = None) -> FakeResult:
        self.executed.append((query, params))
        return self.result


class FakeConnectionContext:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    def __enter__(self) -> FakeConnection:
        return self.connection

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None


class FakeEngine:
    def __init__(self, result: FakeResult) -> None:
        self.connection = FakeConnection(result)

    def connect(self) -> FakeConnectionContext:
        return FakeConnectionContext(self.connection)


def make_table() -> Table:
    metadata = MetaData()
    return Table(
        "orders",
        metadata,
        Column("order_id", BigInteger, primary_key=True),
        Column("amount", Numeric(12, 2), nullable=False),
        schema="raw",
    )


def test_list_tables_returns_raw_and_curated_tables(monkeypatch) -> None:
    monkeypatch.setattr(metadata_tools, "inspect", lambda engine: FakeInspector())

    result = list_tables(engine=object())

    assert [table.model_dump() for table in result] == [
        {"schema_name": "raw", "table_name": "customers"},
        {"schema_name": "raw", "table_name": "orders"},
        {"schema_name": "curated", "table_name": "customer_revenue"},
    ]


def test_inspect_schema_returns_column_information(monkeypatch) -> None:
    monkeypatch.setattr(metadata_tools, "inspect", lambda engine: FakeInspector())

    result = inspect_schema("raw", "orders", engine=object())

    assert result.found is True
    assert result.columns[0].column_name == "order_id"
    assert result.columns[0].primary_key is True
    assert result.columns[0].ordinal_position == 1


def test_inspect_schema_unknown_table_returns_not_found(monkeypatch) -> None:
    monkeypatch.setattr(metadata_tools, "inspect", lambda engine: FakeInspector())

    result = inspect_schema("raw", "missing", engine=object())

    assert result.found is False
    assert result.columns == []


def test_get_table_metadata_returns_metadata() -> None:
    engine = FakeEngine(
        FakeResult(
            [
                {
                    "schema_name": "raw",
                    "table_name": "orders",
                    "table_type": "source",
                    "description": "Raw orders.",
                }
            ]
        )
    )

    result = get_table_metadata("raw", "orders", engine=engine)  # type: ignore[arg-type]

    assert result.table_type == "source"
    assert result.description == "Raw orders."


def test_get_table_metadata_missing_returns_not_found() -> None:
    result = get_table_metadata("raw", "missing", engine=FakeEngine(FakeResult()))  # type: ignore[arg-type]

    assert result.found is False
    assert result.description is None


def test_get_column_metadata_preserves_query_order() -> None:
    engine = FakeEngine(
        FakeResult(
            [
                {
                    "column_name": "order_id",
                    "data_type": "BIGINT",
                    "is_nullable": False,
                    "is_primary_key": True,
                    "description": "Order id.",
                },
                {
                    "column_name": "amount",
                    "data_type": "NUMERIC(12,2)",
                    "is_nullable": False,
                    "is_primary_key": False,
                    "description": "Order amount.",
                },
            ]
        )
    )

    result = get_column_metadata("raw", "orders", engine=engine)  # type: ignore[arg-type]

    assert [column.column_name for column in result.columns] == ["order_id", "amount"]


def test_sample_record_limit_validation() -> None:
    with pytest.raises(InvalidLimitError):
        get_sample_records("raw", "orders", limit=21, engine=object())  # type: ignore[arg-type]

    with pytest.raises(InvalidLimitError):
        get_sample_records("raw", "orders", limit=0, engine=object())  # type: ignore[arg-type]


def test_get_sample_records_uses_reflected_columns_and_serializes_values(monkeypatch) -> None:
    monkeypatch.setattr(metadata_tools, "_reflect_table", lambda schema_name, table_name, engine=None: make_table())
    engine = FakeEngine(FakeResult([{"order_id": 1, "amount": Decimal("12.50"), "created_at": datetime(2024, 1, 1)}]))

    result = get_sample_records("raw", "orders", limit=1, engine=engine)  # type: ignore[arg-type]

    assert result.records == [{"order_id": 1, "amount": "12.50", "created_at": "2024-01-01T00:00:00"}]
    executed_query = engine.connection.executed[0][0]
    assert "SELECT raw.orders.order_id, raw.orders.amount" in str(executed_query)
    assert "*" not in str(executed_query)


def test_get_row_count_returns_count(monkeypatch) -> None:
    monkeypatch.setattr(metadata_tools, "_reflect_table", lambda schema_name, table_name, engine=None: make_table())

    result = get_row_count("raw", "orders", engine=FakeEngine(FakeResult(scalar=35)))  # type: ignore[arg-type]

    assert result.row_count == 35


def test_get_pipeline_metadata_returns_metadata() -> None:
    engine = FakeEngine(
        FakeResult(
            [
                {
                    "pipeline_name": "customer_revenue_daily",
                    "description": "Daily customer revenue.",
                    "source_tables": ["raw.customers", "raw.orders"],
                    "target_table": "curated.customer_revenue",
                    "load_type": "incremental",
                    "schedule": "daily",
                    "is_active": True,
                }
            ]
        )
    )

    result = get_pipeline_metadata("customer_revenue_daily", engine=engine)  # type: ignore[arg-type]

    assert result.source_tables == ["raw.customers", "raw.orders"]
    assert result.is_active is True


def test_safe_identifier_handling_rejects_injection() -> None:
    with pytest.raises(InvalidIdentifierError):
        inspect_schema("raw;drop schema raw", "orders", engine=object())  # type: ignore[arg-type]

    with pytest.raises(InvalidIdentifierError):
        get_table_metadata("raw", "orders;delete", engine=object())  # type: ignore[arg-type]

    with pytest.raises(InvalidIdentifierError):
        get_pipeline_metadata("customer_revenue_daily;drop", engine=object())  # type: ignore[arg-type]


def test_database_failure_is_wrapped(monkeypatch) -> None:
    def raise_operational_error(engine: object) -> object:
        raise OperationalError("SELECT 1", {}, Exception("failed"))

    monkeypatch.setattr(metadata_tools, "inspect", raise_operational_error)

    with pytest.raises(MetadataDatabaseError, match="Database unavailable"):
        list_tables(engine=object())


def test_table_metadata_database_failure_is_wrapped() -> None:
    engine = MagicMock()
    engine.connect.side_effect = OperationalError("SELECT", {}, Exception("failed"))

    with pytest.raises(MetadataDatabaseError):
        get_table_metadata("raw", "orders", engine=engine)
