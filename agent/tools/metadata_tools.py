"""Read-only metadata intelligence tools for PostgreSQL."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import MetaData, Table, func, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from database.connection import get_engine


logger = logging.getLogger(__name__)

USER_TABLE_SCHEMAS = ("raw", "curated")
MAX_SAMPLE_LIMIT = 20
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class MetadataToolError(Exception):
    """Base exception for metadata tool failures."""


class InvalidIdentifierError(MetadataToolError, ValueError):
    """Raised when a schema, table, column, or pipeline identifier is invalid."""


class InvalidLimitError(MetadataToolError, ValueError):
    """Raised when a sample-record limit is outside the allowed range."""


class MetadataDatabaseError(MetadataToolError):
    """Raised when the database cannot service a metadata tool request."""


class TableReference(BaseModel):
    """Reference to a database table."""

    schema_name: str
    table_name: str


class ColumnSchema(BaseModel):
    """Physical column details from PostgreSQL."""

    column_name: str
    data_type: str
    nullable: bool
    primary_key: bool
    ordinal_position: int | None = None


class TableSchemaResult(BaseModel):
    """Schema inspection result for a table."""

    schema_name: str
    table_name: str
    found: bool = True
    columns: list[ColumnSchema] = Field(default_factory=list)


class TableMetadataResult(BaseModel):
    """Business metadata for a table."""

    schema_name: str
    table_name: str
    found: bool = True
    table_type: str | None = None
    description: str | None = None


class ColumnMetadata(BaseModel):
    """Business metadata for a column."""

    column_name: str
    data_type: str
    is_nullable: bool
    is_primary_key: bool
    description: str


class ColumnMetadataResult(BaseModel):
    """Business metadata result for table columns."""

    schema_name: str
    table_name: str
    found: bool = True
    columns: list[ColumnMetadata] = Field(default_factory=list)


class SampleRecordsResult(BaseModel):
    """Sample records from a business table."""

    schema_name: str
    table_name: str
    limit: int
    found: bool = True
    records: list[dict[str, Any]] = Field(default_factory=list)


class RowCountResult(BaseModel):
    """Row count for a business table."""

    schema_name: str
    table_name: str
    found: bool = True
    row_count: int | None = None


class PipelineMetadataResult(BaseModel):
    """Metadata for a configured pipeline."""

    pipeline_name: str
    found: bool = True
    description: str | None = None
    source_tables: list[str] = Field(default_factory=list)
    target_table: str | None = None
    load_type: str | None = None
    schedule: str | None = None
    is_active: bool | None = None


def list_tables(engine: Engine | None = None) -> list[TableReference]:
    """Return available user/business tables from raw and curated schemas."""
    database_engine = engine or get_engine()

    try:
        inspector = inspect(database_engine)
        tables: list[TableReference] = []
        for schema_name in USER_TABLE_SCHEMAS:
            if not inspector.has_schema(schema_name):
                continue
            for table_name in sorted(inspector.get_table_names(schema=schema_name)):
                tables.append(TableReference(schema_name=schema_name, table_name=table_name))
        return tables
    except SQLAlchemyError as exc:
        _raise_database_error("list tables", exc)


def inspect_schema(
    schema_name: str,
    table_name: str,
    engine: Engine | None = None,
) -> TableSchemaResult:
    """Return physical column details for a table."""
    _validate_identifier(schema_name, "schema_name")
    _validate_identifier(table_name, "table_name")
    database_engine = engine or get_engine()

    try:
        inspector = inspect(database_engine)
        if not inspector.has_schema(schema_name) or not inspector.has_table(table_name, schema=schema_name):
            return TableSchemaResult(schema_name=schema_name, table_name=table_name, found=False)

        primary_keys = set(inspector.get_pk_constraint(table_name, schema=schema_name).get("constrained_columns", []))
        columns = [
            ColumnSchema(
                column_name=column["name"],
                data_type=str(column["type"]),
                nullable=bool(column["nullable"]),
                primary_key=column["name"] in primary_keys,
                ordinal_position=index,
            )
            for index, column in enumerate(inspector.get_columns(table_name, schema=schema_name), start=1)
        ]
        return TableSchemaResult(schema_name=schema_name, table_name=table_name, columns=columns)
    except SQLAlchemyError as exc:
        _raise_database_error("inspect schema", exc)


def get_table_metadata(
    schema_name: str,
    table_name: str,
    engine: Engine | None = None,
) -> TableMetadataResult:
    """Read business metadata for a table from metadata.table_metadata."""
    _validate_identifier(schema_name, "schema_name")
    _validate_identifier(table_name, "table_name")
    database_engine = engine or get_engine()

    query = text(
        """
        SELECT schema_name, table_name, table_type, description
        FROM metadata.table_metadata
        WHERE schema_name = :schema_name AND table_name = :table_name
        """
    )
    try:
        with database_engine.connect() as connection:
            row = connection.execute(query, {"schema_name": schema_name, "table_name": table_name}).mappings().first()
        if row is None:
            return TableMetadataResult(schema_name=schema_name, table_name=table_name, found=False)
        return TableMetadataResult(**dict(row))
    except SQLAlchemyError as exc:
        _raise_database_error("get table metadata", exc)


def get_column_metadata(
    schema_name: str,
    table_name: str,
    engine: Engine | None = None,
) -> ColumnMetadataResult:
    """Read column metadata for a table from metadata.column_metadata."""
    _validate_identifier(schema_name, "schema_name")
    _validate_identifier(table_name, "table_name")
    database_engine = engine or get_engine()

    query = text(
        """
        SELECT column_name, data_type, is_nullable, is_primary_key, description
        FROM metadata.column_metadata
        WHERE schema_name = :schema_name AND table_name = :table_name
        ORDER BY id
        """
    )
    try:
        with database_engine.connect() as connection:
            rows = connection.execute(query, {"schema_name": schema_name, "table_name": table_name}).mappings().all()
        if not rows:
            return ColumnMetadataResult(schema_name=schema_name, table_name=table_name, found=False)
        return ColumnMetadataResult(
            schema_name=schema_name,
            table_name=table_name,
            columns=[ColumnMetadata(**dict(row)) for row in rows],
        )
    except SQLAlchemyError as exc:
        _raise_database_error("get column metadata", exc)


def get_sample_records(
    schema_name: str,
    table_name: str,
    limit: int = 5,
    engine: Engine | None = None,
) -> SampleRecordsResult:
    """Return up to 20 read-only sample records from a validated table."""
    _validate_sample_limit(limit)
    table = _reflect_table(schema_name, table_name, engine)
    if table is None:
        return SampleRecordsResult(schema_name=schema_name, table_name=table_name, limit=limit, found=False)

    database_engine = engine or get_engine()
    ordered_columns = list(table.c)
    query = select(*ordered_columns).select_from(table).limit(limit)
    primary_key_columns = [table.c[column.name] for column in table.primary_key.columns]
    if primary_key_columns:
        query = query.order_by(*primary_key_columns)

    try:
        with database_engine.connect() as connection:
            rows = connection.execute(query).mappings().all()
        return SampleRecordsResult(
            schema_name=schema_name,
            table_name=table_name,
            limit=limit,
            records=[{key: _serialize_value(value) for key, value in row.items()} for row in rows],
        )
    except SQLAlchemyError as exc:
        _raise_database_error("get sample records", exc)


def get_row_count(
    schema_name: str,
    table_name: str,
    engine: Engine | None = None,
) -> RowCountResult:
    """Return a read-only row count for a validated table."""
    table = _reflect_table(schema_name, table_name, engine)
    if table is None:
        return RowCountResult(schema_name=schema_name, table_name=table_name, found=False)

    database_engine = engine or get_engine()
    query = select(func.count()).select_from(table)
    try:
        with database_engine.connect() as connection:
            row_count = connection.execute(query).scalar_one()
        return RowCountResult(schema_name=schema_name, table_name=table_name, row_count=int(row_count))
    except SQLAlchemyError as exc:
        _raise_database_error("get row count", exc)


def get_pipeline_metadata(
    pipeline_name: str,
    engine: Engine | None = None,
) -> PipelineMetadataResult:
    """Read configured pipeline metadata without executing the pipeline."""
    _validate_pipeline_name(pipeline_name)
    database_engine = engine or get_engine()

    query = text(
        """
        SELECT pipeline_name, description, source_tables, target_table, load_type, schedule, is_active
        FROM metadata.pipeline_metadata
        WHERE pipeline_name = :pipeline_name
        """
    )
    try:
        with database_engine.connect() as connection:
            row = connection.execute(query, {"pipeline_name": pipeline_name}).mappings().first()
        if row is None:
            return PipelineMetadataResult(pipeline_name=pipeline_name, found=False)

        values = dict(row)
        values["source_tables"] = list(values["source_tables"])
        return PipelineMetadataResult(**values)
    except SQLAlchemyError as exc:
        _raise_database_error("get pipeline metadata", exc)


def _reflect_table(schema_name: str, table_name: str, engine: Engine | None = None) -> Table | None:
    """Reflect a business table only after identifier validation and existence checks."""
    _validate_identifier(schema_name, "schema_name")
    _validate_identifier(table_name, "table_name")
    if schema_name not in USER_TABLE_SCHEMAS:
        return None
    database_engine = engine or get_engine()

    try:
        inspector = inspect(database_engine)
        if not inspector.has_schema(schema_name) or not inspector.has_table(table_name, schema=schema_name):
            return None

        metadata = MetaData()
        return Table(table_name, metadata, schema=schema_name, autoload_with=database_engine)
    except SQLAlchemyError as exc:
        _raise_database_error("reflect table", exc)


def _validate_identifier(value: str, field_name: str) -> None:
    if not IDENTIFIER_PATTERN.fullmatch(value):
        raise InvalidIdentifierError(f"Invalid {field_name}")


def _validate_pipeline_name(pipeline_name: str) -> None:
    if not re.fullmatch(r"^[A-Za-z_][A-Za-z0-9_]*$", pipeline_name):
        raise InvalidIdentifierError("Invalid pipeline_name")


def _validate_sample_limit(limit: int) -> None:
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > MAX_SAMPLE_LIMIT:
        raise InvalidLimitError(f"Sample limit must be between 1 and {MAX_SAMPLE_LIMIT}")


def _serialize_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _raise_database_error(action: str, exc: SQLAlchemyError) -> None:
    logger.warning("Metadata tool failed to %s: %s", action, exc.__class__.__name__)
    raise MetadataDatabaseError("Database unavailable or metadata query failed") from exc
