"""Strict models for generated SQL artifacts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SQLDialect(StrEnum):
    """Supported SQL dialects."""

    POSTGRESQL = "postgresql"


class SQLStatementType(StrEnum):
    """Controlled SQL statement categories for generated pipeline SQL."""

    SELECT = "select"
    INSERT = "insert"
    MERGE = "merge"


class SQLValidationStatus(StrEnum):
    """Local deterministic validation status for generated SQL."""

    VALID = "valid"
    INVALID = "invalid"


class GeneratedSQL(BaseModel):
    """Inspectable generated SQL artifact. The SQL is never executed in Phase 6."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, use_enum_values=True)

    pipeline_name: str
    dialect: SQLDialect
    sql: str = Field(min_length=1)
    source_tables: list[str] = Field(min_length=1)
    target_table: str
    statement_type: SQLStatementType
    validation_status: SQLValidationStatus = SQLValidationStatus.INVALID
    warnings: list[str] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)

    @field_validator("sql")
    @classmethod
    def validate_sql_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("sql must not be empty")
        return value

    @field_validator("source_tables")
    @classmethod
    def validate_source_tables_not_empty(cls, value: list[str]) -> list[str]:
        normalized = [table.strip().lower() for table in value]
        if any(not table for table in normalized):
            raise ValueError("source_tables must not contain empty values")
        return normalized

    @field_validator("target_table")
    @classmethod
    def validate_target_table_not_empty(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("target_table must not be empty")
        return normalized