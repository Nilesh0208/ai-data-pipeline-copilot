"""Strict registry and dispatcher for agent-accessible metadata tools."""

from __future__ import annotations

from typing import Any, Callable

from google.genai import types
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent.tools import metadata_tools


class ToolExecutionResult(BaseModel):
    """Structured result returned by the local tool dispatcher."""

    ok: bool
    result: Any | None = None
    error_type: str | None = None
    message: str | None = None


class ToolTraceEntry(BaseModel):
    """Sanitized operational trace for a tool call."""

    tool_name: str
    arguments: dict[str, Any]
    success: bool
    error_type: str | None = None


class NoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TableArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_name: str = Field(min_length=1)
    table_name: str = Field(min_length=1)


class SampleArgs(TableArgs):
    limit: int = Field(default=5, ge=1, le=10)


class PipelineArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pipeline_name: str = Field(min_length=1)


class RegisteredTool(BaseModel):
    """Metadata needed to expose and execute a function tool."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    description: str
    args_model: type[BaseModel]
    function: Callable[..., Any]

    def gemini_declaration(self) -> types.FunctionDeclaration:
        return types.FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters_json_schema=_gemini_parameters(self.args_model.model_json_schema()),
        )


TOOL_REGISTRY: dict[str, RegisteredTool] = {
    "list_tables": RegisteredTool(
        name="list_tables",
        description="List available read-only business tables in raw and curated schemas.",
        args_model=NoArgs,
        function=metadata_tools.list_tables,
    ),
    "inspect_schema": RegisteredTool(
        name="inspect_schema",
        description="Inspect physical columns, data types, nullability, and primary keys for a table.",
        args_model=TableArgs,
        function=metadata_tools.inspect_schema,
    ),
    "get_table_metadata": RegisteredTool(
        name="get_table_metadata",
        description="Get business metadata and description for a table.",
        args_model=TableArgs,
        function=metadata_tools.get_table_metadata,
    ),
    "get_column_metadata": RegisteredTool(
        name="get_column_metadata",
        description="Get business descriptions for columns in a table.",
        args_model=TableArgs,
        function=metadata_tools.get_column_metadata,
    ),
    "get_sample_records": RegisteredTool(
        name="get_sample_records",
        description="Get bounded sample records for a table. Limit is capped at 10 for agent use.",
        args_model=SampleArgs,
        function=metadata_tools.get_sample_records,
    ),
    "get_row_count": RegisteredTool(
        name="get_row_count",
        description="Get a read-only row count for a table.",
        args_model=TableArgs,
        function=metadata_tools.get_row_count,
    ),
    "get_pipeline_metadata": RegisteredTool(
        name="get_pipeline_metadata",
        description="Get metadata for an existing configured pipeline by pipeline name.",
        args_model=PipelineArgs,
        function=metadata_tools.get_pipeline_metadata,
    ),
}


def get_gemini_tool() -> types.Tool:
    """Return Gemini function declarations for the registered metadata tools."""
    return types.Tool(function_declarations=[tool.gemini_declaration() for tool in TOOL_REGISTRY.values()])


def dispatch_tool(tool_name: str, arguments: dict[str, Any] | None) -> ToolExecutionResult:
    """Validate and execute one registered metadata tool call."""
    tool = TOOL_REGISTRY.get(tool_name)
    if tool is None:
        return ToolExecutionResult(ok=False, error_type="unknown_tool", message=f"Unknown tool: {tool_name}")

    try:
        validated_args = tool.args_model.model_validate(arguments or {})
    except ValidationError as exc:
        return ToolExecutionResult(ok=False, error_type="invalid_arguments", message=str(exc))

    try:
        raw_result = tool.function(**validated_args.model_dump())
    except metadata_tools.MetadataToolError as exc:
        return ToolExecutionResult(ok=False, error_type=exc.__class__.__name__, message=str(exc))

    return ToolExecutionResult(ok=True, result=_serialize_result(raw_result))


def trace_entry(tool_name: str, arguments: dict[str, Any] | None, result: ToolExecutionResult) -> ToolTraceEntry:
    """Build a sanitized trace entry for API responses."""
    return ToolTraceEntry(
        tool_name=tool_name,
        arguments=arguments or {},
        success=result.ok,
        error_type=result.error_type,
    )


def _gemini_parameters(schema: dict[str, Any]) -> dict[str, Any]:
    normalized = _remove_pydantic_noise(schema)
    normalized.setdefault("type", "object")
    normalized.setdefault("properties", {})
    return normalized


def _remove_pydantic_noise(value: Any) -> Any:
    unsupported = {"title", "additionalProperties"}
    if isinstance(value, dict):
        return {key: _remove_pydantic_noise(item) for key, item in value.items() if key not in unsupported}
    if isinstance(value, list):
        return [_remove_pydantic_noise(item) for item in value]
    return value


def _serialize_result(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_serialize_result(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialize_result(item) for key, item in value.items()}
    return value
