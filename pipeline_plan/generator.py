"""Gemini-backed pipeline-plan generation service."""

from __future__ import annotations

import json
import logging
from typing import Any

from google.genai import errors, types
from pydantic import ValidationError

from agent.client import GeminiClientProtocol, create_gemini_client
from agent.provider_errors import log_gemini_error
from config.settings import Settings, get_settings
from pipeline.requirements import PipelineRequirement
from pipeline_plan.models import PipelinePlan
from pipeline_plan.prompts import PIPELINE_PLAN_GENERATION_INSTRUCTIONS
from pipeline_plan.validator import validate_pipeline_plan
from quality.models import GeneratedDataQualityPlan
from sql_generation.models import GeneratedSQL


logger = logging.getLogger(__name__)


class PipelinePlanGenerationError(RuntimeError):
    """Controlled pipeline-plan generation failure."""


class PipelinePlanProviderError(PipelinePlanGenerationError):
    """Raised when Gemini cannot generate a pipeline plan."""

    def __init__(self, message: str, *, http_status: int = 503, request_id: str | None = None) -> None:
        self.http_status = http_status
        self.request_id = request_id
        super().__init__(message)


class PipelinePlanStructuredOutputError(PipelinePlanGenerationError):
    """Raised when Gemini returns malformed pipeline-plan output."""


def generate_pipeline_plan(
    requirement: PipelineRequirement,
    generated_sql: GeneratedSQL,
    quality_plan: GeneratedDataQualityPlan,
    *,
    client: GeminiClientProtocol | None = None,
    settings: Settings | None = None,
) -> PipelinePlan:
    """Generate and locally validate a pipeline plan without executing anything."""
    active_settings = settings or get_settings()
    gemini_client = client or create_gemini_client(active_settings)

    try:
        response = gemini_client.models.generate_content(
            model=active_settings.gemini_model,
            contents=[
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=_build_user_prompt(requirement, generated_sql, quality_plan))],
                )
            ],
            config=_build_generate_content_config(),
        )
    except (errors.ClientError, errors.ServerError, errors.APIError) as exc:
        context = log_gemini_error(logger, "Gemini pipeline-plan generation", exc)
        raise PipelinePlanProviderError(
            context.public_message,
            http_status=context.http_status,
            request_id=context.request_id,
        ) from exc

    generated_plan = _parse_pipeline_plan(response)
    return validate_pipeline_plan(requirement, generated_sql, quality_plan, generated_plan)


def _build_generate_content_config() -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        system_instruction=PIPELINE_PLAN_GENERATION_INSTRUCTIONS,
        response_mime_type="application/json",
        response_schema=_pipeline_plan_response_schema(),
    )


def _pipeline_plan_response_schema() -> dict[str, Any]:
    return _remove_schema_keyword(PipelinePlan.model_json_schema(mode="validation"), "additionalProperties")


def _remove_schema_keyword(value: Any, keyword: str) -> Any:
    if isinstance(value, dict):
        return {key: _remove_schema_keyword(item, keyword) for key, item in value.items() if key != keyword}
    if isinstance(value, list):
        return [_remove_schema_keyword(item, keyword) for item in value]
    return value


def _build_user_prompt(
    requirement: PipelineRequirement,
    generated_sql: GeneratedSQL,
    quality_plan: GeneratedDataQualityPlan,
) -> str:
    return (
        "Generate an inspect-only PipelinePlan from these authoritative artifacts. "
        "Do not execute SQL, quality rules, Python, shell commands, database writes, or infrastructure changes.\n\n"
        f"PipelineRequirement JSON:\n{json.dumps(requirement.model_dump(mode='json'), indent=2)}\n\n"
        f"GeneratedSQL JSON:\n{json.dumps(generated_sql.model_dump(mode='json'), indent=2)}\n\n"
        f"GeneratedDataQualityPlan JSON:\n{json.dumps(quality_plan.model_dump(mode='json'), indent=2)}"
    )


def _parse_pipeline_plan(response: Any) -> PipelinePlan:
    parsed = _get(response, "parsed", None)
    if isinstance(parsed, PipelinePlan):
        return parsed
    if isinstance(parsed, dict):
        return _validate_pipeline_plan_model(parsed)

    output_text = _get(response, "text", None)
    if not output_text:
        raise PipelinePlanStructuredOutputError("Gemini returned no pipeline-plan output")
    try:
        payload = json.loads(output_text)
    except json.JSONDecodeError as exc:
        raise PipelinePlanStructuredOutputError("Gemini returned malformed pipeline-plan JSON") from exc
    return _validate_pipeline_plan_model(payload)


def _validate_pipeline_plan_model(payload: Any) -> PipelinePlan:
    try:
        return PipelinePlan.model_validate(payload)
    except ValidationError as exc:
        raise PipelinePlanStructuredOutputError(f"Gemini returned invalid PipelinePlan: {exc}") from exc


def _get(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)
