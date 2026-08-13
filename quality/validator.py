"""Deterministic validation for generated data-quality plans."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from pipeline.requirements import PipelineRequirement, ScheduleFrequency, TableReference, TransformationType
from quality.models import DataQualityRule, GeneratedDataQualityPlan, QualityRuleType, QualityValidationStatus


@dataclass(frozen=True)
class QualityColumnMetadata:
    """Minimal metadata needed for deterministic quality-rule checks."""

    data_type: str | None = None


QualityMetadataCatalog = Mapping[str, Mapping[str, QualityColumnMetadata | str | None]]

NUMERIC_TYPE_MARKERS = ("int", "numeric", "decimal", "double", "real", "float", "money")
TEMPORAL_TYPE_MARKERS = ("date", "time", "timestamp")
EQUALITY_FILTER_PATTERN = re.compile(
    r"^\s*(?P<column>[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*)\s*=\s*(?P<value>'[^']*'|\"[^\"]*\"|[a-z0-9_ -]+)\s*$",
    re.IGNORECASE,
)
SCHEDULE_FRESHNESS_THRESHOLDS = {
    ScheduleFrequency.HOURLY: {"value": 2, "unit": "hours"},
    ScheduleFrequency.DAILY: {"value": 2, "unit": "days"},
    ScheduleFrequency.WEEKLY: {"value": 14, "unit": "days"},
}


def validate_generated_quality_plan(
    requirement: PipelineRequirement,
    generated_plan: GeneratedDataQualityPlan,
    *,
    metadata_catalog: QualityMetadataCatalog | None = None,
) -> GeneratedDataQualityPlan:
    """Return a quality plan annotated with deterministic validation results."""
    errors: list[str] = []
    warnings = list(generated_plan.warnings)

    allowed_tables = {source.table.qualified_name for source in requirement.sources}
    allowed_tables.add(requirement.target.table.qualified_name)

    if generated_plan.pipeline_name != requirement.pipeline_name:
        errors.append("pipeline_name does not match PipelineRequirement")

    seen_rule_keys: set[tuple[object, ...]] = set()
    for rule in generated_plan.rules:
        table_name = rule.table.qualified_name
        if table_name not in allowed_tables:
            errors.append(f"rule {rule.rule_name} references unrelated table: {table_name}")
            continue

        rule_key = _rule_key(rule)
        if rule_key in seen_rule_keys:
            errors.append(f"duplicate quality rule generated: {rule.rule_name}")
        seen_rule_keys.add(rule_key)

        _validate_rule_semantics(rule, requirement, allowed_tables, errors, warnings)
        _validate_rule_metadata(rule, metadata_catalog, errors, warnings)

    status = _status_for(errors, warnings)
    return generated_plan.model_copy(
        update={
            "validation_status": status,
            "validation_errors": _deduplicate(errors),
            "warnings": _deduplicate(warnings),
        }
    )


def _validate_rule_semantics(
    rule: DataQualityRule,
    requirement: PipelineRequirement,
    allowed_tables: set[str],
    errors: list[str],
    warnings: list[str],
) -> None:
    if rule.rule_type in {
        QualityRuleType.NOT_NULL,
        QualityRuleType.UNIQUE,
        QualityRuleType.ACCEPTED_VALUES,
        QualityRuleType.POSITIVE_VALUE,
        QualityRuleType.RANGE,
        QualityRuleType.FRESHNESS,
    } and rule.column is None:
        errors.append(f"{rule.rule_type} rule {rule.rule_name} requires column")

    if rule.rule_type == QualityRuleType.ACCEPTED_VALUES:
        accepted_values = rule.parameters.get("accepted_values")
        if not isinstance(accepted_values, list) or not accepted_values:
            errors.append(f"accepted_values rule {rule.rule_name} requires non-empty accepted_values parameter")
        elif not _accepted_values_are_requirement_justified(requirement, rule, accepted_values):
            errors.append(f"accepted_values rule {rule.rule_name} values are not justified by PipelineRequirement filters")

    if rule.rule_type == QualityRuleType.RANGE:
        minimum = rule.parameters.get("min")
        maximum = rule.parameters.get("max")
        if minimum is None and maximum is None:
            errors.append(f"range rule {rule.rule_name} requires at least one of min or max")
        if minimum is not None and not _is_number(minimum):
            errors.append(f"range rule {rule.rule_name} min must be numeric")
        if maximum is not None and not _is_number(maximum):
            errors.append(f"range rule {rule.rule_name} max must be numeric")
        if _is_number(minimum) and _is_number(maximum) and float(minimum) > float(maximum):
            errors.append(f"range rule {rule.rule_name} min must be less than or equal to max")

    if rule.rule_type == QualityRuleType.FRESHNESS:
        threshold = rule.parameters.get("threshold")
        if not isinstance(threshold, dict):
            errors.append(f"freshness rule {rule.rule_name} requires structured threshold parameter")
        elif not _valid_threshold(threshold):
            errors.append(f"freshness rule {rule.rule_name} threshold requires positive value and supported unit")
        elif not _freshness_threshold_is_schedule_justified(requirement, threshold):
            errors.append(f"freshness rule {rule.rule_name} threshold is not justified by PipelineRequirement schedule")

    if rule.rule_type == QualityRuleType.REFERENTIAL_INTEGRITY:
        reference = rule.parameters.get("reference")
        if not isinstance(reference, dict):
            errors.append(f"referential_integrity rule {rule.rule_name} requires structured reference parameter")
            return
        if not isinstance(reference.get("table"), dict):
            errors.append(f"referential_integrity rule {rule.rule_name} requires reference.table")
        elif not _reference_table_is_allowed(reference["table"], allowed_tables):
            errors.append(f"referential_integrity rule {rule.rule_name} references unrelated reference.table")
        elif not _reference_is_join_justified(requirement, rule, reference):
            errors.append(f"referential_integrity rule {rule.rule_name} reference is not justified by PipelineRequirement joins")
        if not isinstance(reference.get("column"), str) or not reference.get("column"):
            errors.append(f"referential_integrity rule {rule.rule_name} requires reference.column")

    if rule.rule_type == QualityRuleType.ROW_COUNT:
        minimum = rule.parameters.get("min")
        maximum = rule.parameters.get("max")
        equals = rule.parameters.get("equals")
        if minimum is None and maximum is None and equals is None:
            warnings.append(f"row_count rule {rule.rule_name} has no threshold; inspect before operational use")
        for name, value in (("min", minimum), ("max", maximum), ("equals", equals)):
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                errors.append(f"row_count rule {rule.rule_name} {name} must be a non-negative integer")


def _validate_rule_metadata(
    rule: DataQualityRule,
    metadata_catalog: QualityMetadataCatalog | None,
    errors: list[str],
    warnings: list[str],
) -> None:
    if rule.column is None:
        return
    table_name = rule.table.qualified_name
    columns = metadata_catalog.get(table_name) if metadata_catalog else None
    if columns is None:
        if rule.rule_type in {QualityRuleType.POSITIVE_VALUE, QualityRuleType.RANGE, QualityRuleType.FRESHNESS}:
            warnings.append(f"metadata unavailable for {table_name}; {rule.rule_name} column type cannot be verified")
        return
    if rule.column not in columns:
        errors.append(f"rule {rule.rule_name} references unknown column {table_name}.{rule.column}")
        return

    data_type = _metadata_data_type(columns[rule.column])
    if data_type is None:
        return
    normalized_type = data_type.lower()
    if rule.rule_type in {QualityRuleType.POSITIVE_VALUE, QualityRuleType.RANGE} and not _contains_any(
        normalized_type, NUMERIC_TYPE_MARKERS
    ):
        errors.append(f"rule {rule.rule_name} requires numeric column but {table_name}.{rule.column} is {data_type}")
    if rule.rule_type == QualityRuleType.FRESHNESS and not _contains_any(normalized_type, TEMPORAL_TYPE_MARKERS):
        errors.append(f"rule {rule.rule_name} requires date/timestamp column but {table_name}.{rule.column} is {data_type}")


def _metadata_data_type(value: QualityColumnMetadata | str | None) -> str | None:
    if isinstance(value, QualityColumnMetadata):
        return value.data_type
    return value


def _rule_key(rule: DataQualityRule) -> tuple[object, ...]:
    return (
        rule.rule_type,
        rule.table.qualified_name,
        rule.column,
        _freeze(rule.parameters),
    )


def _freeze(value: object) -> object:
    if isinstance(value, dict):
        return tuple(sorted((key, _freeze(item)) for key, item in value.items()))
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _valid_threshold(threshold: dict[str, object]) -> bool:
    value = threshold.get("value")
    unit = threshold.get("unit")
    return isinstance(value, int) and not isinstance(value, bool) and value > 0 and unit in {"minutes", "hours", "days"}


def _freshness_threshold_is_schedule_justified(
    requirement: PipelineRequirement,
    threshold: dict[str, object],
) -> bool:
    expected = SCHEDULE_FRESHNESS_THRESHOLDS.get(requirement.schedule.frequency)
    return expected == {"value": threshold.get("value"), "unit": threshold.get("unit")}


def _reference_table_is_allowed(reference_table: dict[str, object], allowed_tables: set[str]) -> bool:
    try:
        table = TableReference.model_validate(reference_table)
    except ValueError:
        return False
    return table.qualified_name in allowed_tables


def _reference_is_join_justified(
    requirement: PipelineRequirement,
    rule: DataQualityRule,
    reference: dict[str, object],
) -> bool:
    if rule.column is None or not isinstance(reference.get("table"), dict) or not isinstance(reference.get("column"), str):
        return False
    reference_table = TableReference.model_validate(reference["table"]).qualified_name
    reference_column = reference["column"]
    rule_endpoint = (rule.table.qualified_name, rule.column)
    reference_endpoint = (reference_table, reference_column)
    return any(
        (left == rule_endpoint and right == reference_endpoint) or (right == rule_endpoint and left == reference_endpoint)
        for left, right in _join_column_pairs(requirement)
    )


def _accepted_values_are_requirement_justified(
    requirement: PipelineRequirement,
    rule: DataQualityRule,
    accepted_values: list[object],
) -> bool:
    justified_values = _filter_values_for_rule(requirement, rule)
    return bool(justified_values) and all(value in justified_values for value in accepted_values)


def _filter_values_for_rule(requirement: PipelineRequirement, rule: DataQualityRule) -> list[object]:
    if rule.column is None:
        return []
    alias_tables = _alias_table_map(requirement)
    values: list[object] = []
    for transformation in requirement.transformations:
        if transformation.rule_type != TransformationType.FILTER:
            continue
        equality_filter = _extract_equality_filter(transformation.expression)
        if equality_filter is None:
            continue
        referenced_column = _resolve_column_reference(equality_filter[0], alias_tables)
        if referenced_column == (rule.table.qualified_name, rule.column):
            values.append(equality_filter[1])
    return _unique_values(values)


def _extract_equality_filter(expression: dict[str, object]) -> tuple[str, object] | None:
    operator = str(expression.get("operator", "")).lower()
    if operator in {"equals", "equal", "eq", "="} and "column" in expression and "value" in expression:
        return str(expression["column"]), _normalize_filter_literal(expression["value"])

    for key in ("condition", "predicate", "filter"):
        value = expression.get(key)
        if isinstance(value, str):
            parsed = _parse_equality_filter_text(value)
            if parsed is not None:
                return parsed
    return None


def _parse_equality_filter_text(value: str) -> tuple[str, str] | None:
    match = EQUALITY_FILTER_PATTERN.fullmatch(value)
    if match is None:
        return None
    return match.group("column"), _strip_matching_quotes(match.group("value").strip())


def _normalize_filter_literal(value: object) -> object:
    if isinstance(value, str):
        return _strip_matching_quotes(value.strip())
    return value


def _strip_matching_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _join_column_pairs(requirement: PipelineRequirement) -> list[tuple[tuple[str, str], tuple[str, str]]]:
    alias_tables = _alias_table_map(requirement)
    pairs: list[tuple[tuple[str, str], tuple[str, str]]] = []
    for transformation in requirement.transformations:
        if transformation.rule_type != TransformationType.JOIN:
            continue
        expression = transformation.expression
        left = _resolve_column_reference(expression.get("left_column"), alias_tables)
        right = _resolve_column_reference(expression.get("right_column"), alias_tables)
        if left is None or right is None:
            input_columns = [_resolve_column_reference(column, alias_tables) for column in transformation.input_columns]
            resolved = [column for column in input_columns if column is not None]
            if len(resolved) == 2:
                left, right = resolved
        if left is not None and right is not None:
            pairs.append((left, right))
    return pairs


def _alias_table_map(requirement: PipelineRequirement) -> dict[str, str]:
    alias_tables: dict[str, str] = {}
    for source in requirement.sources:
        alias_tables[source.table.table_name] = source.table.qualified_name
        if source.alias is not None:
            alias_tables[source.alias] = source.table.qualified_name
    target = requirement.target.table
    alias_tables[target.table_name] = target.qualified_name
    return alias_tables


def _resolve_column_reference(value: object, alias_tables: dict[str, str]) -> tuple[str, str] | None:
    if not isinstance(value, str) or "." not in value:
        return None
    qualifier, column = value.lower().split(".", 1)
    table_name = alias_tables.get(qualifier)
    if table_name is None:
        return None
    return table_name, column


def _unique_values(values: list[object]) -> list[object]:
    unique: list[object] = []
    for value in values:
        if value not in unique:
            unique.append(value)
    return unique


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _contains_any(value: str, markers: tuple[str, ...]) -> bool:
    return any(marker in value for marker in markers)


def _status_for(errors: list[str], warnings: list[str]) -> QualityValidationStatus:
    if errors:
        return QualityValidationStatus.INVALID
    if warnings:
        return QualityValidationStatus.VALID_WITH_WARNINGS
    return QualityValidationStatus.VALID


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))

