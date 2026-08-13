"""Strict models for generated pipeline-plan artifacts."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pipeline.requirements import PipelineRequirement, normalize_identifier
from quality.models import GeneratedDataQualityPlan
from sql_generation.models import GeneratedSQL


EXECUTABLE_SQL_PATTERNS = (
    re.compile(r"\bselect\b.+\bfrom\b", re.IGNORECASE),
    re.compile(r"\binsert\s+into\b", re.IGNORECASE),
    re.compile(r"\bupdate\s+[a-z_][a-z0-9_.]*\s+set\b", re.IGNORECASE),
    re.compile(r"\bdelete\s+from\b", re.IGNORECASE),
    re.compile(r"\bmerge\s+into\b", re.IGNORECASE),
    re.compile(r"\bdrop\s+(table|schema|database|user|role)\b", re.IGNORECASE),
    re.compile(r"\balter\s+(table|schema|database|user|role)\b", re.IGNORECASE),
    re.compile(r"\btruncate\s+(table\s+)?[a-z_][a-z0-9_.]*\b", re.IGNORECASE),
    re.compile(r"\bcreate\s+(table|view|schema|database|user|role)\b", re.IGNORECASE),
    re.compile(r"\bgrant\s+.+\s+on\b", re.IGNORECASE),
    re.compile(r"\brevoke\s+.+\s+on\b", re.IGNORECASE),
)
EXECUTABLE_PYTHON_PATTERNS = (
    re.compile(r"\bimport\s+(os|subprocess|sys)\b", re.IGNORECASE),
    re.compile(r"\bos\.system\s*\(", re.IGNORECASE),
    re.compile(r"\bsubprocess\.(run|popen|call)\s*\(", re.IGNORECASE),
    re.compile(r"\beval\s*\(", re.IGNORECASE),
    re.compile(r"\bexec\s*\(", re.IGNORECASE),
)
EXECUTABLE_SHELL_PATTERNS = (
    re.compile(r"\brm\s+-rf\b", re.IGNORECASE),
    re.compile(r"\bpowershell(?:\.exe)?\b", re.IGNORECASE),
    re.compile(r"\bcmd\.exe\b", re.IGNORECASE),
    re.compile(r"\bbash\s+(-c|<|\S*\.sh\b)", re.IGNORECASE),
    re.compile(r"\bcurl\b.+\|\s*(sh|bash)\b", re.IGNORECASE),
    re.compile(r"\bwget\b.+\|\s*(sh|bash)\b", re.IGNORECASE),
)
UNSAFE_CONTENT_MARKERS = ("--", "/*", "*/", "$(", "`")


class PipelinePlanValidationStatus(StrEnum):
    """Local deterministic validation status for generated pipeline plans."""

    VALID = "valid"
    VALID_WITH_WARNINGS = "valid_with_warnings"
    INVALID = "invalid"


class PipelinePlanStepType(StrEnum):
    """Focused step categories for inspect-only pipeline planning."""

    SOURCE_VALIDATION = "source_validation"
    EXTRACTION = "extraction"
    TRANSFORMATION = "transformation"
    LOAD = "load"
    QUALITY_VALIDATION = "quality_validation"
    AUDIT = "audit"
    MONITORING = "monitoring"


class RetryBackoffStrategy(StrEnum):
    """Supported retry backoff descriptions."""

    NONE = "none"
    FIXED = "fixed"
    EXPONENTIAL = "exponential"


class FailureBehavior(StrEnum):
    """Supported inspect-only failure behavior."""

    FAIL_PIPELINE = "fail_pipeline"
    STOP_DOWNSTREAM = "stop_downstream"
    CONTINUE_WITH_WARNING = "continue_with_warning"


class RetryPolicy(BaseModel):
    """Bounded retry settings for planning only."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, use_enum_values=True)

    max_attempts: int = Field(ge=1, le=5)
    delay_seconds: int = Field(ge=0, le=3600)
    backoff_strategy: RetryBackoffStrategy = RetryBackoffStrategy.NONE


class ScheduleSummary(BaseModel):
    """Structured schedule summary copied from the requirement."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, use_enum_values=True)

    frequency: str
    timezone: str
    enabled: bool


class ObservabilityPlan(BaseModel):
    """Implementation-neutral observability expectations."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, use_enum_values=True)

    metrics: list[str] = Field(default_factory=list)
    logs: list[str] = Field(default_factory=list)

    @field_validator("metrics", "logs")
    @classmethod
    def validate_safe_values(cls, value: list[str]) -> list[str]:
        return [_validate_safe_text(item) for item in value]


class PipelinePlanStep(BaseModel):
    """Single inspectable pipeline-plan step. It is never executed in Phase 8."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, use_enum_values=True)

    step_id: str
    step_type: PipelinePlanStepType
    description: str = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    retry_policy: RetryPolicy
    failure_behavior: FailureBehavior

    @field_validator("step_id")
    @classmethod
    def validate_step_id(cls, value: str) -> str:
        return normalize_identifier(value, "step_id")

    @field_validator("depends_on")
    @classmethod
    def validate_depends_on(cls, value: list[str]) -> list[str]:
        normalized = [normalize_identifier(item, "depends_on") for item in value]
        if len(normalized) != len(set(normalized)):
            raise ValueError("depends_on must not contain duplicates")
        return normalized

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        return _validate_safe_text(value)

    @field_validator("inputs", "outputs")
    @classmethod
    def validate_references(cls, value: list[str]) -> list[str]:
        return [_validate_reference(item) for item in value]


class PipelinePlan(BaseModel):
    """Generated pipeline-plan artifact. The plan is inspect-only."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, use_enum_values=True)

    pipeline_name: str
    description: str = Field(min_length=1)
    schedule: ScheduleSummary
    execution_steps: list[PipelinePlanStep] = Field(min_length=1)
    quality_checks: list[str] = Field(default_factory=list)
    observability: ObservabilityPlan
    validation_status: PipelinePlanValidationStatus = PipelinePlanValidationStatus.INVALID
    warnings: list[str] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)

    @field_validator("pipeline_name")
    @classmethod
    def validate_pipeline_name(cls, value: str) -> str:
        return normalize_identifier(value, "pipeline_name")

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        return _validate_safe_text(value)

    @field_validator("quality_checks")
    @classmethod
    def validate_quality_checks(cls, value: list[str]) -> list[str]:
        normalized = [normalize_identifier(item, "quality_checks") for item in value]
        if len(normalized) != len(set(normalized)):
            raise ValueError("quality_checks must not contain duplicates")
        return normalized


class PipelinePlanGenerationRequest(BaseModel):
    """Strict API request grouping the authoritative upstream artifacts."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, use_enum_values=True)

    requirement: PipelineRequirement
    generated_sql: GeneratedSQL
    quality_plan: GeneratedDataQualityPlan

    @model_validator(mode="after")
    def validate_artifact_names(self) -> Self:
        if self.generated_sql.pipeline_name != self.requirement.pipeline_name:
            raise ValueError("generated_sql pipeline_name does not match requirement")
        if self.quality_plan.pipeline_name != self.requirement.pipeline_name:
            raise ValueError("quality_plan pipeline_name does not match requirement")
        return self


def _validate_reference(value: str) -> str:
    normalized = _validate_safe_text(value).strip().lower()
    if not normalized:
        raise ValueError("references must not contain empty values")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)?", normalized):
        raise ValueError("references must be identifiers or schema-qualified table names")
    return normalized


def _validate_safe_text(value: str) -> str:
    executable_patterns = EXECUTABLE_SQL_PATTERNS + EXECUTABLE_PYTHON_PATTERNS + EXECUTABLE_SHELL_PATTERNS
    if any(marker in value for marker in UNSAFE_CONTENT_MARKERS) or any(
        pattern.search(value) for pattern in executable_patterns
    ):
        raise ValueError("pipeline plan content must not contain executable SQL, Python, or shell payloads")
    return value
