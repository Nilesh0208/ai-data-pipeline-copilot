"""Gemini function-calling loop for generating validated pipeline requirements."""

from __future__ import annotations

import json
import logging
import traceback
from typing import Any

from google.genai import errors, types
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent.client import GeminiClientProtocol, MissingGeminiAPIKeyError, create_gemini_client
from agent.prompts import PIPELINE_AGENT_INSTRUCTIONS, REQUIREMENT_CORRECTION_INSTRUCTIONS
from agent.provider_errors import extract_provider_request_id, log_gemini_error
from agent.tool_registry import ToolTraceEntry, dispatch_tool, get_gemini_tool, trace_entry
from config.settings import Settings, get_settings
from pipeline.requirements import PipelineRequirement


logger = logging.getLogger(__name__)
MAX_TOOL_ITERATIONS = 8


class PipelineAgentError(RuntimeError):
    """Controlled agent failure."""


class PipelineRequirementValidationError(PipelineAgentError):
    """Raised when model output fails local PipelineRequirement validation."""

    def __init__(self, validation_error: ValidationError) -> None:
        self.validation_error = validation_error
        super().__init__(f"Model returned invalid PipelineRequirement: {validation_error}")


class PipelineAgentRequest(BaseModel):
    """HTTP request payload for requirement generation."""

    model_config = ConfigDict(str_strip_whitespace=True)

    request: str = Field(min_length=1)


class PipelineAgentResult(BaseModel):
    """Application-level agent result."""

    status: str
    requirement: PipelineRequirement | None = None
    tools_used: list[str] = Field(default_factory=list)
    message: str
    trace: list[ToolTraceEntry] = Field(default_factory=list)
    model: str | None = None
    request_id: str | None = None


def generate_pipeline_requirement(
    user_request: str,
    *,
    client: GeminiClientProtocol | None = None,
    settings: Settings | None = None,
    max_tool_iterations: int = MAX_TOOL_ITERATIONS,
) -> PipelineAgentResult:
    """Generate a validated PipelineRequirement from a natural-language request."""
    active_settings = settings or get_settings()
    gemini_client = client or create_gemini_client(active_settings)
    model = active_settings.gemini_model
    trace: list[ToolTraceEntry] = []
    tools_used: list[str] = []
    correction_attempted = False

    contents: list[types.Content] = [
        types.Content(role="user", parts=[types.Part.from_text(text=user_request)]),
    ]
    config = _build_generate_content_config()

    for _ in range(max_tool_iterations + 1):
        response = gemini_client.models.generate_content(model=model, contents=contents, config=config)
        function_calls = _extract_function_calls(response)
        if not function_calls:
            try:
                requirement = _parse_requirement(response)
            except PipelineRequirementValidationError as exc:
                if correction_attempted:
                    raise PipelineAgentError(f"Corrected PipelineRequirement is still invalid: {exc.validation_error}") from exc
                correction_attempted = True
                model_content = _extract_model_content(response)
                if model_content is not None:
                    contents.append(model_content)
                contents.append(_build_correction_content(exc.validation_error))
                continue
            return PipelineAgentResult(
                status="success",
                requirement=requirement,
                tools_used=tools_used,
                message="Pipeline requirement generated successfully",
                trace=trace,
                model=model,
                request_id=_extract_response_id(response),
            )

        if len(trace) >= max_tool_iterations:
            raise PipelineAgentError("Maximum tool iterations exceeded")

        model_content = _extract_model_content(response)
        if model_content is not None:
            contents.append(model_content)

        function_response_parts: list[types.Part] = []
        for call in function_calls:
            if len(trace) >= max_tool_iterations:
                raise PipelineAgentError("Maximum tool iterations exceeded")
            result = dispatch_tool(call["name"], call["arguments"])
            trace.append(trace_entry(call["name"], call["arguments"], result))
            if call["name"] not in tools_used:
                tools_used.append(call["name"])
            function_response_parts.append(_build_function_response_part(call, result.model_dump(mode="json")))

        contents.append(types.Content(role="user", parts=function_response_parts))

    raise PipelineAgentError("Maximum tool iterations exceeded")


def generate_pipeline_requirement_result(
    user_request: str,
    *,
    client: GeminiClientProtocol | None = None,
    settings: Settings | None = None,
) -> PipelineAgentResult:
    """Generate a requirement and convert known failures into result objects."""
    active_settings = settings or get_settings()
    try:
        return generate_pipeline_requirement(user_request, client=client, settings=active_settings)
    except MissingGeminiAPIKeyError as exc:
        return PipelineAgentResult(status="error", message=str(exc), model=active_settings.gemini_model)
    except PipelineAgentError as exc:
        return PipelineAgentResult(status="error", message=str(exc), model=active_settings.gemini_model)
    except ValueError as exc:
        _log_value_error(exc, active_settings)
        return PipelineAgentResult(
            status="error",
            message="AI agent failed: ValueError",
            model=active_settings.gemini_model,
        )
    except (errors.ClientError, errors.ServerError, errors.APIError) as exc:
        context = log_gemini_error(logger, "Gemini requirement generation", exc)
        return PipelineAgentResult(
            status="error",
            message=context.public_message,
            model=active_settings.gemini_model,
            request_id=context.request_id,
        )
    except Exception as exc:
        logger.warning("Gemini agent failed: error_type=%s", exc.__class__.__name__)
        return PipelineAgentResult(
            status="error",
            message=f"AI agent failed: {exc.__class__.__name__}",
            model=active_settings.gemini_model,
        )


def _build_generate_content_config() -> types.GenerateContentConfig:
    """Build the non-secret Gemini generation configuration."""
    return types.GenerateContentConfig(
        system_instruction=PIPELINE_AGENT_INSTRUCTIONS,
        tools=[get_gemini_tool()],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        response_mime_type="application/json",
        response_schema=_pipeline_requirement_response_schema(),
    )


def _pipeline_requirement_response_schema() -> dict[str, Any]:
    return _remove_schema_keyword(PipelineRequirement.model_json_schema(mode="validation"), "additionalProperties")


def _remove_schema_keyword(value: Any, keyword: str) -> Any:
    if isinstance(value, dict):
        return {key: _remove_schema_keyword(item, keyword) for key, item in value.items() if key != keyword}
    if isinstance(value, list):
        return [_remove_schema_keyword(item, keyword) for item in value]
    return value


def _extract_function_calls(response: Any) -> list[dict[str, Any]]:
    function_calls = _get(response, "function_calls", None) or []
    calls: list[dict[str, Any]] = []
    for call in function_calls:
        calls.append(
            {
                "id": _get(call, "id", None),
                "name": str(_get(call, "name", "")),
                "arguments": _normalize_function_args(_get(call, "args", {})),
            }
        )
    return calls


def _build_function_response_part(call: dict[str, Any], response: dict[str, Any]) -> types.Part:
    """Build a Gemini function response part, preserving function call id when present."""
    return types.Part(
        function_response=types.FunctionResponse(
            id=str(call["id"]) if call.get("id") else None,
            name=call["name"],
            response={"tool_result": response},
        )
    )


def _build_correction_content(validation_error: ValidationError) -> types.Content:
    return types.Content(
        role="user",
        parts=[
            types.Part.from_text(
                text=REQUIREMENT_CORRECTION_INSTRUCTIONS.format(
                    validation_errors=_format_validation_errors(validation_error)
                )
            )
        ],
    )


def _format_validation_errors(validation_error: ValidationError) -> str:
    return json.dumps(validation_error.errors(include_url=False), indent=2, default=str)


def _normalize_function_args(arguments: Any) -> dict[str, Any]:
    if arguments is None:
        return {}
    if isinstance(arguments, dict):
        return dict(arguments)
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            return {"__invalid_json__": arguments}
        return parsed if isinstance(parsed, dict) else {"__invalid_json__": arguments}
    try:
        return dict(arguments)
    except (TypeError, ValueError):
        return {"__invalid_args__": str(arguments)}


def _extract_model_content(response: Any) -> types.Content | None:
    candidates = _get(response, "candidates", None) or []
    if not candidates:
        return None
    return _get(candidates[0], "content", None)


def _parse_requirement(response: Any) -> PipelineRequirement:
    parsed = _get(response, "parsed", None)
    if isinstance(parsed, PipelineRequirement):
        return parsed
    if isinstance(parsed, dict):
        return _validate_requirement(parsed)

    output_text = _get(response, "text", None)
    if not output_text:
        raise PipelineAgentError("Model returned no structured PipelineRequirement output")
    try:
        payload = json.loads(output_text)
    except json.JSONDecodeError as exc:
        raise PipelineAgentError("Model returned invalid structured JSON") from exc
    return _validate_requirement(payload)


def _validate_requirement(payload: Any) -> PipelineRequirement:
    try:
        return PipelineRequirement.model_validate(payload)
    except ValidationError as exc:
        raise PipelineRequirementValidationError(exc) from exc


def _log_value_error(exc: ValueError, settings: Settings) -> None:
    frame = _last_traceback_frame(exc)
    logger.warning(
        "Gemini agent ValueError: error_type=%s message=%s source_file=%s source_function=%s",
        exc.__class__.__name__,
        _safe_error_message(exc),
        frame.filename if frame else None,
        frame.name if frame else None,
        exc_info=settings.app_env.lower() in {"local", "development", "dev"},
    )


def _last_traceback_frame(exc: BaseException) -> traceback.FrameSummary | None:
    frames = traceback.extract_tb(exc.__traceback__) if exc.__traceback__ else []
    return frames[-1] if frames else None


def _log_gemini_error(exc: errors.APIError) -> None:
    log_gemini_error(logger, "Gemini requirement generation", exc)


def _safe_error_message(exc: BaseException) -> str:
    message = getattr(exc, "message", None) or str(exc)
    return str(message)


def _extract_error_request_id(exc: BaseException) -> str | None:
    return extract_provider_request_id(exc)


def _extract_response_id(response: Any) -> str | None:
    response_id = _get(response, "response_id", None)
    return str(response_id) if response_id else None


def _get(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)
