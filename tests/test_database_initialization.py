"""Tests for Phase 2 database initialization support."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import init_database, verify_database


class FakeConnection:
    def __init__(self) -> None:
        self.executed_sql: list[str] = []

    def exec_driver_sql(self, sql: str) -> None:
        self.executed_sql.append(sql)


class FakeBeginContext:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    def __enter__(self) -> FakeConnection:
        return self.connection

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None


class FakeEngine:
    def __init__(self) -> None:
        self.connection = FakeConnection()
        self.begin_calls = 0

    def begin(self) -> FakeBeginContext:
        self.begin_calls += 1
        return FakeBeginContext(self.connection)


def test_phase_2_sql_files_are_discoverable_in_order() -> None:
    sql_files = init_database.discover_sql_files()

    assert [path.name for path in sql_files] == [
        "001_create_schemas.sql",
        "002_create_business_tables.sql",
        "003_create_metadata_tables.sql",
        "004_seed_sample_data.sql",
        "005_seed_metadata.sql",
    ]


def test_discover_sql_files_raises_when_directory_is_missing(tmp_path: Path) -> None:
    missing_directory = tmp_path / "missing"

    with pytest.raises(FileNotFoundError):
        init_database.discover_sql_files(missing_directory)


def test_initialize_database_executes_sql_files_in_order(tmp_path: Path) -> None:
    first_file = tmp_path / "001_first.sql"
    second_file = tmp_path / "002_second.sql"
    first_file.write_text("SELECT 1;", encoding="utf-8")
    second_file.write_text("SELECT 2;", encoding="utf-8")
    engine = FakeEngine()

    executed_files = init_database.initialize_database(engine=engine, sql_directory=tmp_path)

    assert executed_files == [first_file, second_file]
    assert engine.begin_calls == 2
    assert engine.connection.executed_sql == ["SELECT 1;", "SELECT 2;"]


def test_initialize_database_uses_centralized_engine(monkeypatch, tmp_path: Path) -> None:
    sql_file = tmp_path / "001_test.sql"
    sql_file.write_text("SELECT 1;", encoding="utf-8")
    engine = FakeEngine()

    monkeypatch.setattr(init_database, "get_engine", lambda: engine)

    init_database.initialize_database(sql_directory=tmp_path)

    assert engine.connection.executed_sql == ["SELECT 1;"]


def test_metadata_sql_contains_expected_phase_2_definitions() -> None:
    metadata_sql = (init_database.SQL_DIRECTORY / "005_seed_metadata.sql").read_text(encoding="utf-8")

    assert "raw', 'customers'" in metadata_sql
    assert "raw', 'orders'" in metadata_sql
    assert "curated', 'customer_revenue'" in metadata_sql
    assert "customer_revenue_daily" in metadata_sql
    assert "ARRAY['raw.customers', 'raw.orders']" in metadata_sql
    assert "curated.customer_revenue" in metadata_sql
    assert "incremental" in metadata_sql
    assert "daily" in metadata_sql


def test_sample_seed_sql_is_idempotent() -> None:
    sample_sql = (init_database.SQL_DIRECTORY / "004_seed_sample_data.sql").read_text(encoding="utf-8")
    metadata_sql = (init_database.SQL_DIRECTORY / "005_seed_metadata.sql").read_text(encoding="utf-8")

    assert sample_sql.count("ON CONFLICT") == 2
    assert metadata_sql.count("ON CONFLICT") == 3


def test_verification_expectations_match_phase_2_scope() -> None:
    assert verify_database.EXPECTED_SCHEMAS == ("raw", "curated", "metadata")
    assert verify_database.EXPECTED_COUNTS == {
        "raw.customers": 10,
        "raw.orders": 35,
        "metadata.table_metadata": 3,
        "metadata.column_metadata": 20,
        "metadata.pipeline_metadata": 1,
        "metadata.pipeline_runs": 0,
    }
    assert ("curated", "customer_revenue") in verify_database.EXPECTED_TABLES