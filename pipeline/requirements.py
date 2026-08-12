"""Validated pipeline requirement contract for future agent output."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


IDENTIFIER_PATTERN = re.compile(r"^[a-z_][a-z0-9_]*$")
COLUMN_REFERENCE_PATTERN = re.compile(r"^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)?$")
SQL_KEYWORD_PATTERN = re.compile(
    r"\b(select|insert|update|delete|drop|alter|create|truncate|merge|grant|revoke|execute)\b",
    re.IGNORECASE,
)
UNSAFE_EXPRESSION_MARKERS = (";", "--", "/*", "*/")


class StrictRequirementModel(BaseModel):
    """Base model for strict, serializable requirement configuration."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, use_enum_values=True)


class WriteMode(StrEnum):
    """Allowed target write modes."""

    APPEND = "append"
    OVERWRITE = "overwrite"
    MERGE = "merge"


class TransformationType(StrEnum):
    """Allowed transformation rule categories."""

    FILTER = "filter"
    JOIN = "join"
    AGGREGATE = "aggregate"
    DERIVE = "derive"
    RENAME = "rename"


class LoadType(StrEnum):
    """Allowed load behavior categories."""

    FULL = "full"
    INCREMENTAL = "incremental"


class ScheduleFrequency(StrEnum):
    """Allowed schedule frequencies."""

    MANUAL = "manual"
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"


class TableReference(StrictRequirementModel):
    """Reference to a database table without arbitrary SQL."""

    schema_name: str
    table_name: str

    @field_validator("schema_name", "table_name", mode="before")
    @classmethod
    def validate_identifier(cls, value: object, info: Any) -> str:
        return normalize_identifier(value, info.field_name)

    @property
    def qualified_name(self) -> str:
        """Return schema-qualified table name."""
        return f"{self.schema_name}.{self.table_name}"


class PipelineSource(StrictRequirementModel):
    """Source table used by a pipeline requirement."""

    table: TableReference
    alias: str | None = None
    description: str | None = None

    @field_validator("alias")
    @classmethod
    def validate_alias(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_identifier(value, "alias")


class PipelineTarget(StrictRequirementModel):
    """Target table and controlled write behavior."""

    table: TableReference
    write_mode: WriteMode


class TransformationRule(StrictRequirementModel):
    """Declarative transformation rule; expressions are configuration only."""

    rule_type: TransformationType
    description: str = Field(min_length=1)
    input_columns: list[str] = Field(default_factory=list)
    output_column: str | None = None
    expression: dict[str, Any] = Field(default_factory=dict)

    @field_validator("input_columns")
    @classmethod
    def validate_input_columns(cls, value: list[str]) -> list[str]:
        return [normalize_column_reference(column, "input_columns") for column in value]

    @field_validator("output_column")
    @classmethod
    def validate_output_column(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_identifier(value, "output_column")

    @field_validator("expression")
    @classmethod
    def validate_expression(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_expression_config(value)

    @model_validator(mode="after")
    def validate_rule_consistency(self) -> Self:
        if self.rule_type in {TransformationType.DERIVE, TransformationType.RENAME, TransformationType.AGGREGATE}:
            if self.output_column is None:
                raise ValueError(f"{self.rule_type} transformation requires output_column")
        if self.rule_type == TransformationType.JOIN and len(self.input_columns) < 2:
            raise ValueError("join transformation requires at least two input_columns")
        return self


class LoadStrategy(StrictRequirementModel):
    """Declarative load behavior without execution."""

    load_type: LoadType
    incremental_column: str | None = None
    watermark_column: str | None = None
    deduplication_keys: list[str] = Field(default_factory=list)

    @field_validator("incremental_column", "watermark_column")
    @classmethod
    def validate_optional_column(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        return normalize_identifier(value, info.field_name)

    @field_validator("deduplication_keys")
    @classmethod
    def validate_deduplication_keys(cls, value: list[str]) -> list[str]:
        normalized = [normalize_identifier(key, "deduplication_keys") for key in value]
        if len(normalized) != len(set(normalized)):
            raise ValueError("deduplication_keys must not contain duplicates")
        return normalized

    @model_validator(mode="after")
    def validate_load_consistency(self) -> Self:
        has_incremental_marker = self.incremental_column is not None or self.watermark_column is not None
        if self.load_type == LoadType.INCREMENTAL and not has_incremental_marker:
            raise ValueError("incremental load requires incremental_column or watermark_column")
        if self.load_type == LoadType.FULL and has_incremental_marker:
            raise ValueError("full load cannot define incremental_column or watermark_column")
        if self.load_type == LoadType.FULL and self.deduplication_keys:
            raise ValueError("full load cannot define deduplication_keys")
        return self


class ScheduleDefinition(StrictRequirementModel):
    """Configuration-only schedule definition."""

    frequency: ScheduleFrequency
    timezone: str = "UTC"
    enabled: bool = True

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        if not value:
            raise ValueError("timezone must not be empty")
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value

    @model_validator(mode="after")
    def validate_schedule_consistency(self) -> Self:
        if self.frequency == ScheduleFrequency.MANUAL and self.enabled:
            raise ValueError("manual schedules must set enabled to false")
        return self


class PipelineRequirement(StrictRequirementModel):
    """Strict, structured representation of a requested data pipeline."""

    pipeline_name: str
    description: str | None = None
    sources: list[PipelineSource] = Field(min_length=1)
    target: PipelineTarget
    transformations: list[TransformationRule] = Field(default_factory=list)
    load_strategy: LoadStrategy
    schedule: ScheduleDefinition
    business_purpose: str | None = None
    owner: str | None = None
    tags: list[str] = Field(default_factory=list)

    @field_validator("pipeline_name")
    @classmethod
    def validate_pipeline_name(cls, value: str) -> str:
        return normalize_identifier(value, "pipeline_name")

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: list[str]) -> list[str]:
        normalized = [normalize_identifier(tag, "tags") for tag in value]
        if len(normalized) != len(set(normalized)):
            raise ValueError("tags must not contain duplicates")
        return normalized

    @model_validator(mode="after")
    def validate_requirement_consistency(self) -> Self:
        source_tables = [source.table.qualified_name for source in self.sources]
        if len(source_tables) != len(set(source_tables)):
            raise ValueError("duplicate source table references are not allowed")

        aliases = [source.alias for source in self.sources if source.alias is not None]
        if len(aliases) != len(set(aliases)):
            raise ValueError("source aliases must be unique")

        is_pass_through = len(source_tables) == 1 and source_tables[0] == self.target.table.qualified_name
        if not self.transformations and not is_pass_through:
            raise ValueError("transformations are required unless the requirement is a same-table pass-through")

        return self


def normalize_identifier(value: object, field_name: str) -> str:
    """Normalize and validate a database-safe identifier."""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    normalized = value.strip().lower()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    if not IDENTIFIER_PATTERN.fullmatch(normalized):
        raise ValueError(f"invalid identifier for {field_name}")
    return normalized


def normalize_column_reference(value: object, field_name: str) -> str:
    """Normalize and validate a column or alias-qualified column reference."""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must contain strings")
    normalized = value.strip().lower()
    if not normalized:
        raise ValueError(f"{field_name} must not contain empty values")
    if not COLUMN_REFERENCE_PATTERN.fullmatch(normalized):
        raise ValueError(f"invalid column reference for {field_name}")
    return normalized


def validate_expression_config(value: dict[str, Any]) -> dict[str, Any]:
    """Reject SQL-like expression strings while preserving structured config."""
    if not isinstance(value, dict):
        raise ValueError("expression must be an object")
    _validate_expression_value(value)
    return value


def _validate_expression_value(value: Any) -> None:
    if isinstance(value, dict):
        for key, nested_value in value.items():
            normalize_identifier(key, "expression keys")
            _validate_expression_value(nested_value)
        return
    if isinstance(value, list):
        for nested_value in value:
            _validate_expression_value(nested_value)
        return
    if isinstance(value, str):
        if any(marker in value for marker in UNSAFE_EXPRESSION_MARKERS) or SQL_KEYWORD_PATTERN.search(value):
            raise ValueError("expression must not contain SQL statements or unsafe SQL markers")
        return
    if value is None or isinstance(value, bool | int | float):
        return
    raise ValueError("expression values must be strings, numbers, booleans, null, arrays, or objects")
