"""Gemini-backed data-quality rule generation service."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from google.genai import errors, types
from pydantic import ValidationError

from agent.client import GeminiClientProtocol, create_gemini_client
from agent.provider_errors import log_gemini_error
from config.settings import Settings, get_settings
from pipeline.requirements import PipelineRequirement, ScheduleFrequency, TransformationType
from quality.models import DataQualityRule, GeneratedDataQualityPlan, QualityRuleType
from quality.prompts import QUALITY_GENERATION_INSTRUCTIONS
from quality.validator import QualityMetadataCatalog, validate_generated_quality_plan


logger = logging.getLogger(__name__)
EQUALITY_FILTER_PATTERN = re.compile(
    r"^\s*(?P<column>[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*)\s*=\s*(?P<value>'[^']*'|\"[^\"]*\"|[a-z0-9_ -]+)\s*$",
    re.IGNORECASE,
)
SCHEDULE_FRESHNESS_THRESHOLDS = {
    ScheduleFrequency.HOURLY: {"value": 2, "unit": "hours"},
    ScheduleFrequency.DAILY: {"value": 2, "unit": "days"},
    ScheduleFrequency.WEEKLY: {"value": 14, "unit": "days"},
}


class QualityGenerationError(RuntimeError):
    """Controlled quality generation failure."""


class QualityProviderError(QualityGenerationError):
    """Raised when Gemini cannot generate quality rules."""

    def __init__(self, message: str, *, http_status: int = 503, request_id: str | None = None) -> None:
        self.http_status = http_status
        self.request_id = request_id
        super().__init__(message)


class QualityStructuredOutputError(QualityGenerationError):
    """Raised when Gemini returns malformed generated-quality output."""


def generate_quality_plan(
    requirement: PipelineRequirement,
    *,
    client: GeminiClientProtocol | None = None,
    settings: Settings | None = None,
    metadata_catalog: QualityMetadataCatalog | None = None,
) -> GeneratedDataQualityPlan:
    """Generate and locally validate data-quality rules without executing them."""
    active_settings = settings or get_settings()
    gemini_client = client or create_gemini_client(active_settings)
    contents = [
        types.Content(role="user", parts=[types.Part.from_text(text=_build_user_prompt(requirement))]),
    ]

    response = _generate_content(gemini_client, active_settings, contents)
    generated_plan = _parse_generated_quality_plan(response)
    generated_plan = normalize_quality_plan(requirement, generated_plan)
    validated_plan = validate_generated_quality_plan(requirement, generated_plan, metadata_catalog=metadata_catalog)

    if validated_plan.validation_status != "invalid":
        return validated_plan

    model_content = _extract_model_content(response)
    if model_content is not None:
        contents.append(model_content)
    contents.append(_build_correction_content(requirement, validated_plan))

    correction_response = _generate_content(gemini_client, active_settings, contents)
    corrected_plan = _parse_generated_quality_plan(correction_response)
    corrected_plan = normalize_quality_plan(requirement, corrected_plan)
    return validate_generated_quality_plan(requirement, corrected_plan, metadata_catalog=metadata_catalog)


def normalize_quality_plan(
    requirement: PipelineRequirement,
    generated_plan: GeneratedDataQualityPlan,
) -> GeneratedDataQualityPlan:
    """Enrich generated quality rules using only deterministic PipelineRequirement facts."""
    normalized_rules = [_normalize_rule(requirement, rule) for rule in generated_plan.rules]
    return generated_plan.model_copy(
        update={
            "rules": normalized_rules,
            "validation_status": "invalid",
            "validation_errors": [],
        }
    )


def _normalize_rule(requirement: PipelineRequirement, rule: DataQualityRule) -> DataQualityRule:
    parameters = dict(rule.parameters)

    if rule.rule_type == QualityRuleType.FRESHNESS and "threshold" not in parameters:
        threshold = SCHEDULE_FRESHNESS_THRESHOLDS.get(requirement.schedule.frequency)
        if threshold is not None and rule.column is not None and _table_allowed(requirement, rule.table.qualified_name):
            parameters["threshold"] = dict(threshold)

    if rule.rule_type == QualityRuleType.REFERENTIAL_INTEGRITY and "reference" not in parameters:
        reference = _infer_reference(requirement, rule)
        if reference is not None:
            parameters["reference"] = reference

    if rule.rule_type == QualityRuleType.ACCEPTED_VALUES and "accepted_values" not in parameters:
        accepted_values = _infer_accepted_values(requirement, rule)
        if accepted_values:
            parameters["accepted_values"] = accepted_values

    return rule.model_copy(update={"parameters": parameters})


def _table_allowed(requirement: PipelineRequirement, table_name: str) -> bool:
    allowed_tables = {source.table.qualified_name for source in requirement.sources}
    allowed_tables.add(requirement.target.table.qualified_name)
    return table_name in allowed_tables


def _infer_reference(requirement: PipelineRequirement, rule: DataQualityRule) -> dict[str, object] | None:
    if rule.column is None:
        return None
    candidates: list[dict[str, object]] = []
    rule_table = rule.table.qualified_name
    for left, right in _join_column_pairs(requirement):
        if left == (rule_table, rule.column):
            candidates.append(_reference_payload(right[0], right[1]))
        if right == (rule_table, rule.column):
            candidates.append(_reference_payload(left[0], left[1]))
    unique_candidates = _unique_dicts(candidates)
    return unique_candidates[0] if len(unique_candidates) == 1 else None


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


def _reference_payload(table_name: str, column: str) -> dict[str, object]:
    schema_name, table = table_name.split(".", 1)
    return {"table": {"schema_name": schema_name, "table_name": table}, "column": column}


def _infer_accepted_values(requirement: PipelineRequirement, rule: DataQualityRule) -> list[object]:
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
        if referenced_column != (rule.table.qualified_name, rule.column):
            continue
        values.append(equality_filter[1])
    return _unique_values(values)


def _extract_equality_filter(expression: dict[str, Any]) -> tuple[str, object] | None:
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


def _unique_values(values: list[object]) -> list[object]:
    unique: list[object] = []
    for value in values:
        if value not in unique:
            unique.append(value)
    return unique


def _unique_dicts(values: list[dict[str, object]]) -> list[dict[str, object]]:
    unique: list[dict[str, object]] = []
    for value in values:
        if value not in unique:
            unique.append(value)
    return unique


def _generate_content(
    gemini_client: GeminiClientProtocol,
    active_settings: Settings,
    contents: list[types.Content],
) -> Any:
    try:
        return gemini_client.models.generate_content(
            model=active_settings.gemini_model,
            contents=contents,
            config=_build_generate_content_config(),
        )
    except (errors.ClientError, errors.ServerError, errors.APIError) as exc:
        context = log_gemini_error(logger, "Gemini quality generation", exc)
        raise QualityProviderError(
            context.public_message,
            http_status=context.http_status,
            request_id=context.request_id,
        ) from exc


def _build_generate_content_config() -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        system_instruction=QUALITY_GENERATION_INSTRUCTIONS,
        response_mime_type="application/json",
        response_schema=_generated_quality_response_schema(),
    )


def _generated_quality_response_schema() -> dict[str, Any]:
    return _remove_schema_keyword(GeneratedDataQualityPlan.model_json_schema(mode="validation"), "additionalProperties")


def _remove_schema_keyword(value: Any, keyword: str) -> Any:
    if isinstance(value, dict):
        return {key: _remove_schema_keyword(item, keyword) for key, item in value.items() if key != keyword}
    if isinstance(value, list):
        return [_remove_schema_keyword(item, keyword) for item in value]
    return value


def _build_user_prompt(requirement: PipelineRequirement) -> str:
    return (
        "Generate inspect-only data-quality rules for this validated PipelineRequirement. "
        "Do not generate or execute SQL, Python, shell commands, or database writes.\n\n"
        f"PipelineRequirement JSON:\n{json.dumps(requirement.model_dump(mode='json'), indent=2)}"
    )


def _build_correction_content(
    requirement: PipelineRequirement,
    invalid_plan: GeneratedDataQualityPlan,
) -> types.Content:
    return types.Content(
        role="user",
        parts=[
            types.Part.from_text(
                text=(
                    "The GeneratedDataQualityPlan failed local deterministic semantic validation. "
                    "Return exactly one corrected GeneratedDataQualityPlan as JSON. "
                    "Do not execute rules, SQL, Python, shell commands, database writes, remediation, or alerting. "
                    "Gemini self-declared validation_status, warnings, and validation_errors are not authoritative; "
                    "fix the local validation errors below while preserving rules that are already justified.\n\n"
                    f"PipelineRequirement JSON:\n{json.dumps(requirement.model_dump(mode='json'), indent=2)}\n\n"
                    f"Invalid GeneratedDataQualityPlan JSON:\n{json.dumps(invalid_plan.model_dump(mode='json'), indent=2)}\n\n"
                    f"Local validation errors:\n{json.dumps(invalid_plan.validation_errors, indent=2)}"
                )
            )
        ],
    )


def _parse_generated_quality_plan(response: Any) -> GeneratedDataQualityPlan:
    parsed = _get(response, "parsed", None)
    if isinstance(parsed, GeneratedDataQualityPlan):
        return parsed
    if isinstance(parsed, dict):
        return _validate_generated_quality_model(parsed)

    output_text = _get(response, "text", None)
    if not output_text:
        raise QualityStructuredOutputError("Gemini returned no generated quality-plan output")
    try:
        payload = json.loads(output_text)
    except json.JSONDecodeError as exc:
        raise QualityStructuredOutputError("Gemini returned malformed generated quality-plan JSON") from exc
    return _validate_generated_quality_model(payload)


def _validate_generated_quality_model(payload: Any) -> GeneratedDataQualityPlan:
    try:
        return GeneratedDataQualityPlan.model_validate(payload)
    except ValidationError as exc:
        raise QualityStructuredOutputError(f"Gemini returned invalid GeneratedDataQualityPlan: {exc}") from exc


def _extract_model_content(response: Any) -> types.Content | None:
    candidates = _get(response, "candidates", None) or []
    if not candidates:
        return None
    return _get(candidates[0], "content", None)


def _get(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)
