"""Deterministic guardrails for generated SQL."""

from __future__ import annotations

import re

from pipeline.requirements import PipelineRequirement, WriteMode
from sql_generation.models import GeneratedSQL, SQLDialect, SQLStatementType, SQLValidationStatus


PROHIBITED_PATTERNS = (
    re.compile(r"\bdrop\b", re.IGNORECASE),
    re.compile(r"\balter\b", re.IGNORECASE),
    re.compile(r"\btruncate\b", re.IGNORECASE),
    re.compile(r"\bgrant\b", re.IGNORECASE),
    re.compile(r"\brevoke\b", re.IGNORECASE),
    re.compile(r"\bcreate\s+(user|role|database|schema)\b", re.IGNORECASE),
    re.compile(r"\bdrop\s+(database|schema|user|role)\b", re.IGNORECASE),
    re.compile(r"\balter\s+(database|schema|user|role)\b", re.IGNORECASE),
)
QUALIFIED_TABLE_PATTERN = re.compile(r"\b(raw|curated)\.([a-z_][a-z0-9_]*)\b", re.IGNORECASE)


def validate_generated_sql(requirement: PipelineRequirement, generated_sql: GeneratedSQL) -> GeneratedSQL:
    """Return a GeneratedSQL artifact annotated with deterministic validation results."""
    errors: list[str] = []
    warnings = list(generated_sql.warnings)

    expected_sources = [source.table.qualified_name for source in requirement.sources]
    expected_target = requirement.target.table.qualified_name
    normalized_sql = generated_sql.sql.strip()

    if generated_sql.pipeline_name != requirement.pipeline_name:
        errors.append("pipeline_name does not match PipelineRequirement")
    if generated_sql.dialect != SQLDialect.POSTGRESQL:
        errors.append("dialect must be postgresql")
    if generated_sql.source_tables != expected_sources:
        errors.append("source_tables do not match PipelineRequirement sources")
    if generated_sql.target_table != expected_target:
        errors.append("target_table does not match PipelineRequirement target")
    if not normalized_sql:
        errors.append("sql must not be empty")

    for pattern in PROHIBITED_PATTERNS:
        if pattern.search(normalized_sql):
            errors.append(f"prohibited SQL construct detected: {pattern.pattern}")

    if _has_multiple_statements(normalized_sql):
        errors.append("generated SQL must contain one statement only")

    referenced_tables = _referenced_qualified_tables(normalized_sql)
    allowed_tables = set(expected_sources + [expected_target])
    unrelated_tables = sorted(referenced_tables - allowed_tables)
    if unrelated_tables:
        errors.append(f"generated SQL references unrelated tables: {', '.join(unrelated_tables)}")

    for source_table in expected_sources:
        if source_table not in referenced_tables:
            errors.append(f"generated SQL does not reference required source table: {source_table}")
    if expected_target not in referenced_tables:
        errors.append(f"generated SQL does not reference target table: {expected_target}")

    statement_type = _infer_statement_type(normalized_sql)
    if statement_type is not None and statement_type != generated_sql.statement_type:
        errors.append("statement_type does not match generated SQL")

    if requirement.target.write_mode == WriteMode.MERGE:
        if generated_sql.statement_type != SQLStatementType.MERGE:
            warnings.append("Pipeline target write_mode is merge; generated SQL is not a MERGE statement")
        if not requirement.load_strategy.deduplication_keys:
            warnings.append("Merge write mode has no deduplication_keys; match keys cannot be verified from requirement")
    if requirement.target.write_mode == WriteMode.APPEND and generated_sql.statement_type != SQLStatementType.INSERT:
        warnings.append("Pipeline target write_mode is append; generated SQL is not an INSERT statement")
    if requirement.target.write_mode == WriteMode.OVERWRITE:
        warnings.append("Overwrite write mode is inspect-only in Phase 6; destructive replacement SQL is prohibited")

    status = SQLValidationStatus.INVALID if errors else SQLValidationStatus.VALID
    return generated_sql.model_copy(
        update={
            "validation_status": status,
            "validation_errors": errors,
            "warnings": _deduplicate(warnings),
        }
    )


def _referenced_qualified_tables(sql: str) -> set[str]:
    return {f"{schema.lower()}.{table.lower()}" for schema, table in QUALIFIED_TABLE_PATTERN.findall(sql)}


def _has_multiple_statements(sql: str) -> bool:
    without_trailing = sql.strip().rstrip(";")
    return ";" in without_trailing


def _infer_statement_type(sql: str) -> SQLStatementType | None:
    first_keyword = _first_keyword(sql)
    if first_keyword == "with":
        lowered = sql.lower()
        if "insert into" in lowered:
            return SQLStatementType.INSERT
        if "merge into" in lowered:
            return SQLStatementType.MERGE
        return SQLStatementType.SELECT
    if first_keyword == "select":
        return SQLStatementType.SELECT
    if first_keyword == "insert":
        return SQLStatementType.INSERT
    if first_keyword == "merge":
        return SQLStatementType.MERGE
    return None


def _first_keyword(sql: str) -> str | None:
    match = re.search(r"\b[a-z]+\b", sql, re.IGNORECASE)
    return match.group(0).lower() if match else None


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))