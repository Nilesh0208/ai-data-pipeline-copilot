"""Gemini-backed SQL generation service for validated pipeline requirements."""

from __future__ import annotations

import json
import logging
from typing import Any

from google.genai import errors, types
from pydantic import ValidationError

from agent.client import GeminiClientProtocol, create_gemini_client
from config.settings import Settings, get_settings
from pipeline.requirements import PipelineRequirement
from sql_generation.models import GeneratedSQL
from sql_generation.prompts import SQL_GENERATION_INSTRUCTIONS
from sql_generation.validator import validate_generated_sql


logger = logging.getLogger(__name__)


class SQLGenerationError(RuntimeError):
    """Controlled SQL generation failure."""


class SQLProviderError(SQLGenerationError):
    """Raised when Gemini cannot generate SQL."""


class SQLStructuredOutputError(SQLGenerationError):
    """Raised when Gemini returns malformed generated-SQL output."""


def generate_sql(
    requirement: PipelineRequirement,
    *,
    client: GeminiClientProtocol | None = None,
    settings: Settings | None = None,
) -> GeneratedSQL:
    """Generate and locally validate PostgreSQL SQL without executing it."""
    active_settings = settings or get_settings()
    gemini_client = client or create_gemini_client(active_settings)

    try:
        response = gemini_client.models.generate_content(
            model=active_settings.gemini_model,
            contents=[types.Content(role="user", parts=[types.Part.from_text(text=_build_user_prompt(requirement))])],
            config=_build_generate_content_config(),
        )
    except (errors.ClientError, errors.ServerError, errors.APIError) as exc:
        logger.warning(
            "Gemini SQL generation failed: error_type=%s status_code=%s code=%s message=%s",
            exc.__class__.__name__,
            getattr(exc, "status_code", None),
            getattr(exc, "code", None),
            _safe_error_message(exc),
        )
        raise SQLProviderError("Gemini SQL generation failed") from exc

    generated_sql = _parse_generated_sql(response)
    return validate_generated_sql(requirement, generated_sql)


def _build_generate_content_config() -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        system_instruction=SQL_GENERATION_INSTRUCTIONS,
        response_mime_type="application/json",
        response_schema=_generated_sql_response_schema(),
    )


def _generated_sql_response_schema() -> dict[str, Any]:
    return _remove_schema_keyword(GeneratedSQL.model_json_schema(mode="validation"), "additionalProperties")


def _remove_schema_keyword(value: Any, keyword: str) -> Any:
    if isinstance(value, dict):
        return {key: _remove_schema_keyword(item, keyword) for key, item in value.items() if key != keyword}
    if isinstance(value, list):
        return [_remove_schema_keyword(item, keyword) for item in value]
    return value


def _build_user_prompt(requirement: PipelineRequirement) -> str:
    return (
        "Generate PostgreSQL SQL for this validated PipelineRequirement. "
        "The SQL is for inspection only and must not be executed.\n\n"
        f"PipelineRequirement JSON:\n{json.dumps(requirement.model_dump(mode='json'), indent=2)}"
    )


def _parse_generated_sql(response: Any) -> GeneratedSQL:
    parsed = _get(response, "parsed", None)
    if isinstance(parsed, GeneratedSQL):
        return parsed
    if isinstance(parsed, dict):
        return _validate_generated_sql_model(parsed)

    output_text = _get(response, "text", None)
    if not output_text:
        raise SQLStructuredOutputError("Gemini returned no generated SQL output")
    try:
        payload = json.loads(output_text)
    except json.JSONDecodeError as exc:
        raise SQLStructuredOutputError("Gemini returned malformed generated SQL JSON") from exc
    return _validate_generated_sql_model(payload)


def _validate_generated_sql_model(payload: Any) -> GeneratedSQL:
    try:
        return GeneratedSQL.model_validate(payload)
    except ValidationError as exc:
        raise SQLStructuredOutputError(f"Gemini returned invalid GeneratedSQL: {exc}") from exc


def _safe_error_message(exc: BaseException) -> str:
    message = getattr(exc, "message", None) or str(exc)
    return str(message)


def _get(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)