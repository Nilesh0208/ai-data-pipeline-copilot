"""Strict models for generated data-quality rule artifacts."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pipeline.requirements import TableReference, normalize_identifier


UNSAFE_CONTENT_PATTERN = re.compile(
    r"\b(select|insert|update|delete|drop|alter|create|truncate|execute|import|subprocess|powershell|bash|cmd\.exe)\b",
    re.IGNORECASE,
)
UNSAFE_CONTENT_MARKERS = (";", "--", "/*", "*/", "$(", "`")


class QualityRuleType(StrEnum):
    """Supported generated data-quality rule categories."""

    NOT_NULL = "not_null"
    UNIQUE = "unique"
    ACCEPTED_VALUES = "accepted_values"
    POSITIVE_VALUE = "positive_value"
    RANGE = "range"
    FRESHNESS = "freshness"
    REFERENTIAL_INTEGRITY = "referential_integrity"
    ROW_COUNT = "row_count"


class QualityRuleSeverity(StrEnum):
    """Controlled quality-rule severity levels."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class QualityValidationStatus(StrEnum):
    """Local deterministic validation status for generated quality plans."""

    VALID = "valid"
    VALID_WITH_WARNINGS = "valid_with_warnings"
    INVALID = "invalid"


class DataQualityRule(BaseModel):
    """Inspectable data-quality rule. Rules are never executed in Phase 7."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, use_enum_values=True)

    rule_name: str
    rule_type: QualityRuleType
    table: TableReference
    column: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    severity: QualityRuleSeverity
    description: str = Field(min_length=1)

    @field_validator("rule_name")
    @classmethod
    def validate_rule_name(cls, value: str) -> str:
        return normalize_identifier(value, "rule_name")

    @field_validator("column")
    @classmethod
    def validate_column(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_identifier(value, "column")

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        _validate_safe_content(value)
        return value

    @field_validator("parameters")
    @classmethod
    def validate_parameters(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("parameters must be an object")
        _validate_parameter_value(value)
        return value

    @model_validator(mode="after")
    def validate_rule_shape(self) -> Self:
        if self.rule_type == QualityRuleType.ROW_COUNT and self.column is not None:
            raise ValueError("row_count rules are table-level and must not define column")
        return self


class GeneratedDataQualityPlan(BaseModel):
    """Generated quality-plan artifact. Validation is local and inspect-only."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, use_enum_values=True)

    pipeline_name: str
    rules: list[DataQualityRule] = Field(default_factory=list)
    validation_status: QualityValidationStatus = QualityValidationStatus.INVALID
    warnings: list[str] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)

    @field_validator("pipeline_name")
    @classmethod
    def validate_pipeline_name(cls, value: str) -> str:
        return normalize_identifier(value, "pipeline_name")


def _validate_parameter_value(value: Any) -> None:
    if isinstance(value, dict):
        for key, nested_value in value.items():
            normalize_identifier(key, "parameter keys")
            _validate_parameter_value(nested_value)
        return
    if isinstance(value, list):
        for nested_value in value:
            _validate_parameter_value(nested_value)
        return
    if isinstance(value, str):
        _validate_safe_content(value)
        return
    if value is None or isinstance(value, bool | int | float):
        return
    raise ValueError("parameters must contain strings, numbers, booleans, null, arrays, or objects")


def _validate_safe_content(value: str) -> None:
    if any(marker in value for marker in UNSAFE_CONTENT_MARKERS) or UNSAFE_CONTENT_PATTERN.search(value):
        raise ValueError("quality rule content must not contain executable SQL, Python, or shell payloads")
