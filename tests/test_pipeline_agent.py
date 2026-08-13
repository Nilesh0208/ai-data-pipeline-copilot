"""Tests for Phase 5 Gemini agent core without real Gemini API calls."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from google.genai import errors, types

from agent import pipeline_agent
from agent.client import MissingGeminiAPIKeyError, create_gemini_client
from agent.pipeline_agent import PipelineAgentError, generate_pipeline_requirement, generate_pipeline_requirement_result
from agent.tool_registry import TOOL_REGISTRY, dispatch_tool, get_gemini_tool
from agent.tools import metadata_tools
from app.main import app
from config.settings import Settings
from pipeline.examples import customer_revenue_daily_requirement


class FakeModels:
    def __init__(self, responses: list[object] | None = None, error: Exception | None = None) -> None:
        self._responses = responses or []
        self.error = error
        self.calls: list[dict[str, object]] = []

    def generate_content(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        assert self._responses, "Unexpected Gemini API call"
        return self._responses.pop(0)


class FakeClient:
    def __init__(self, responses: list[object] | None = None, error: Exception | None = None) -> None:
        self.models = FakeModels(responses, error)


def settings() -> Settings:
    return Settings(GEMINI_API_KEY="test-key", GEMINI_MODEL="test-model")


def final_response(payload: dict[str, object] | None = None, response_id: str = "gemini_final") -> object:
    requirement = payload or customer_revenue_daily_requirement().model_dump(mode="json")
    return SimpleNamespace(response_id=response_id, function_calls=[], text=json.dumps(requirement), parsed=None)


def tool_response(name: str, arguments: dict[str, object] | None = None, response_id: str = "gemini_tool", call_id: str | None = "call_1") -> object:
    model_content = types.Content(
        role="model",
        parts=[types.Part(function_call=types.FunctionCall(id=call_id, name=name, args=arguments or {}))],
    )
    return SimpleNamespace(
        response_id=response_id,
        function_calls=[SimpleNamespace(id=call_id, name=name, args=arguments or {})],
        candidates=[SimpleNamespace(content=model_content)],
        text=None,
        parsed=None,
    )


def test_missing_gemini_api_key_fails_clearly() -> None:
    with pytest.raises(MissingGeminiAPIKeyError, match="GEMINI_API_KEY"):
        create_gemini_client(Settings(GEMINI_API_KEY=""))


def test_gemini_client_factory_uses_configured_key(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class DummyClient:
        def __init__(self, api_key: str) -> None:
            captured["api_key"] = api_key

    from google import genai

    monkeypatch.setattr(genai, "Client", DummyClient)

    client = create_gemini_client(settings())

    assert isinstance(client, DummyClient)
    assert captured["api_key"] == "test-key"


def test_successful_agent_result_without_tools() -> None:
    client = FakeClient([final_response()])

    result = generate_pipeline_requirement("Create customer revenue pipeline", client=client, settings=settings())

    assert result.status == "success"
    assert result.requirement is not None
    assert result.requirement.pipeline_name == "customer_revenue_daily"
    assert result.model == "test-model"
    assert result.request_id == "gemini_final"
    assert client.models.calls[0]["model"] == "test-model"
    assert isinstance(client.models.calls[0]["config"], types.GenerateContentConfig)


def test_tool_registry_exposes_expected_metadata_tools() -> None:
    tool = get_gemini_tool()
    tool_names = {declaration.name for declaration in tool.function_declarations or []}

    assert {
        "list_tables",
        "inspect_schema",
        "get_table_metadata",
        "get_column_metadata",
        "get_sample_records",
        "get_row_count",
        "get_pipeline_metadata",
    }.issubset(tool_names)
    assert "execute_sql" not in tool_names


def test_gemini_tool_schema_validation() -> None:
    tool = get_gemini_tool()
    sample_tool = next(declaration for declaration in tool.function_declarations or [] if declaration.name == "get_sample_records")
    schema = sample_tool.parameters_json_schema

    assert schema["type"] == "object"
    assert set(schema["required"]) == {"schema_name", "table_name"}
    assert schema["properties"]["limit"]["maximum"] == 10
    assert "additionalProperties" not in schema


def test_structured_output_config_uses_pipeline_requirement_schema() -> None:
    config = pipeline_agent._build_generate_content_config()

    assert config.response_mime_type == "application/json"
    assert config.response_schema is not None
    assert config.tools
    assert config.automatic_function_calling is not None
    assert config.automatic_function_calling.disable is True


def test_list_tables_function_call(monkeypatch) -> None:
    monkeypatch.setattr(metadata_tools, "list_tables", lambda: [metadata_tools.TableReference(schema_name="raw", table_name="orders")])
    TOOL_REGISTRY["list_tables"].function = metadata_tools.list_tables
    client = FakeClient([tool_response("list_tables"), final_response()])

    result = generate_pipeline_requirement("Use orders", client=client, settings=settings())

    assert result.status == "success"
    assert result.tools_used == ["list_tables"]
    assert result.trace[0].success is True
    assert len(client.models.calls) == 2
    assert len(client.models.calls[1]["contents"]) == 3
    tool_content = client.models.calls[1]["contents"][-1]
    assert tool_content.role == "user"


def test_inspect_schema_function_call(monkeypatch) -> None:
    monkeypatch.setattr(
        metadata_tools,
        "inspect_schema",
        lambda schema_name, table_name: metadata_tools.TableSchemaResult(schema_name=schema_name, table_name=table_name),
    )
    TOOL_REGISTRY["inspect_schema"].function = metadata_tools.inspect_schema
    client = FakeClient([tool_response("inspect_schema", {"schema_name": "raw", "table_name": "orders"}), final_response()])

    result = generate_pipeline_requirement("Inspect orders", client=client, settings=settings())

    assert result.tools_used == ["inspect_schema"]
    assert result.trace[0].arguments == {"schema_name": "raw", "table_name": "orders"}


def test_multiple_sequential_function_calls(monkeypatch) -> None:
    monkeypatch.setattr(metadata_tools, "list_tables", lambda: [])
    monkeypatch.setattr(
        metadata_tools,
        "inspect_schema",
        lambda schema_name, table_name: metadata_tools.TableSchemaResult(schema_name=schema_name, table_name=table_name),
    )
    TOOL_REGISTRY["list_tables"].function = metadata_tools.list_tables
    TOOL_REGISTRY["inspect_schema"].function = metadata_tools.inspect_schema
    client = FakeClient(
        [
            tool_response("list_tables", response_id="gemini_1"),
            tool_response("inspect_schema", {"schema_name": "raw", "table_name": "orders"}, response_id="gemini_2"),
            final_response(response_id="gemini_3"),
        ]
    )

    result = generate_pipeline_requirement("Join customers and orders", client=client, settings=settings())

    assert result.tools_used == ["list_tables", "inspect_schema"]
    assert len(result.trace) == 2
    assert len(client.models.calls) == 3


def test_dispatcher_rejects_unknown_tool() -> None:
    result = dispatch_tool("import_os", {})

    assert result.ok is False
    assert result.error_type == "unknown_tool"


def test_dispatcher_rejects_invalid_arguments() -> None:
    result = dispatch_tool("inspect_schema", {"schema_name": "raw"})

    assert result.ok is False
    assert result.error_type == "invalid_arguments"


def test_tool_execution_failure_handling(monkeypatch) -> None:
    def fail() -> None:
        raise metadata_tools.MetadataDatabaseError("Database unavailable or metadata query failed")

    monkeypatch.setattr(metadata_tools, "list_tables", fail)
    TOOL_REGISTRY["list_tables"].function = metadata_tools.list_tables

    result = dispatch_tool("list_tables", {})

    assert result.ok is False
    assert result.error_type == "MetadataDatabaseError"


def test_maximum_tool_iteration_handling(monkeypatch) -> None:
    monkeypatch.setattr(metadata_tools, "list_tables", lambda: [])
    TOOL_REGISTRY["list_tables"].function = metadata_tools.list_tables
    client = FakeClient([tool_response("list_tables", response_id=f"gemini_{index}") for index in range(3)])

    with pytest.raises(PipelineAgentError, match="Maximum tool iterations"):
        generate_pipeline_requirement("Loop", client=client, settings=settings(), max_tool_iterations=2)


def test_valid_pipeline_requirement_final_output() -> None:
    client = FakeClient([final_response()])

    result = generate_pipeline_requirement("Create customer revenue pipeline", client=client, settings=settings())

    assert result.requirement is not None
    assert result.requirement.target.table.table_name == "customer_revenue"


def test_invalid_structured_output_rejected() -> None:
    client = FakeClient([SimpleNamespace(response_id="gemini_bad", function_calls=[], text="{not valid json", parsed=None)])

    result = generate_pipeline_requirement_result("Create invalid", client=client, settings=settings())

    assert result.status == "error"
    assert "invalid structured JSON" in result.message


def test_api_success_response(monkeypatch) -> None:
    requirement = customer_revenue_daily_requirement()

    def fake_generate(user_request: str) -> pipeline_agent.PipelineAgentResult:
        return pipeline_agent.PipelineAgentResult(
            status="success",
            requirement=requirement,
            tools_used=["list_tables"],
            message="Pipeline requirement generated successfully",
        )

    monkeypatch.setattr("app.agent.generate_pipeline_requirement_result", fake_generate)
    client = TestClient(app)

    response = client.post("/agent/requirements", json={"request": "Create customer revenue"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["requirement"]["pipeline_name"] == "customer_revenue_daily"


def test_api_invalid_request() -> None:
    client = TestClient(app)

    response = client.post("/agent/requirements", json={"request": ""})

    assert response.status_code == 422


def test_api_missing_gemini_key_returns_controlled_error(monkeypatch) -> None:
    monkeypatch.setattr(pipeline_agent, "get_settings", lambda: Settings(GEMINI_API_KEY=""))
    client = TestClient(app)

    response = client.post("/agent/requirements", json={"request": "Create customer revenue"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "error"
    assert "GEMINI_API_KEY" in payload["message"]


def test_api_controlled_gemini_provider_failure(monkeypatch) -> None:
    def fake_generate(user_request: str) -> pipeline_agent.PipelineAgentResult:
        return pipeline_agent.PipelineAgentResult(status="error", message="Gemini requirement generation failed with Gemini")

    monkeypatch.setattr("app.agent.generate_pipeline_requirement_result", fake_generate)
    client = TestClient(app)

    response = client.post("/agent/requirements", json={"request": "Create customer revenue"})

    assert response.status_code == 200
    assert response.json()["status"] == "error"


def test_gemini_provider_error_logged_without_public_details(caplog) -> None:
    error = errors.ClientError(400, {"error": {"message": "Invalid Gemini function schema"}})
    client = FakeClient(error=error)

    with caplog.at_level("WARNING"):
        result = generate_pipeline_requirement_result("Create customer revenue", client=client, settings=settings())

    assert result.status == "error"
    assert result.message == "Gemini requirement generation failed with Gemini"
    assert "Invalid Gemini function schema" not in result.message
    assert "Gemini requirement generation failed" in caplog.text
    assert "provider=gemini" in caplog.text


def test_no_legacy_provider_dependency_import_required() -> None:
    legacy_provider = "op" + "enai"
    dependency_text = "\n".join([
        __import__("pathlib").Path("requirements.txt").read_text(),
        __import__("pathlib").Path("pyproject.toml").read_text(),
    ]).lower()

    assert legacy_provider not in dependency_text

def test_function_response_preserves_gemini_call_id() -> None:
    part = pipeline_agent._build_function_response_part(
        {"id": "call_123", "name": "list_tables", "arguments": {}},
        {"ok": True, "result": []},
    )

    assert part.function_response is not None
    assert part.function_response.id == "call_123"
    assert part.function_response.name == "list_tables"


def test_value_error_logs_safe_diagnostics(caplog) -> None:
    client = FakeClient(error=ValueError("additionalProperties is only supported in Gemini Enterprise Agent Platform mode"))

    with caplog.at_level("WARNING"):
        result = generate_pipeline_requirement_result("Create customer revenue", client=client, settings=settings())

    assert result.status == "error"
    assert result.message == "AI agent failed: ValueError"
    assert "Gemini agent ValueError" in caplog.text
    assert "ValueError" in caplog.text
    assert "additionalProperties" in caplog.text
    assert "source_file" in caplog.text
    assert "generate_content" in caplog.text

def test_no_generated_gemini_content_uses_tool_role(monkeypatch) -> None:
    monkeypatch.setattr(metadata_tools, "list_tables", lambda: [])
    TOOL_REGISTRY["list_tables"].function = metadata_tools.list_tables
    client = FakeClient([tool_response("list_tables"), final_response()])

    generate_pipeline_requirement("Use orders", client=client, settings=settings())

    for call in client.models.calls:
        for content in call["contents"]:
            assert content.role != "tool"


def test_model_function_call_content_is_preserved(monkeypatch) -> None:
    monkeypatch.setattr(metadata_tools, "list_tables", lambda: [])
    TOOL_REGISTRY["list_tables"].function = metadata_tools.list_tables
    client = FakeClient([tool_response("list_tables", call_id="call_preserve"), final_response()])

    generate_pipeline_requirement("Use orders", client=client, settings=settings())

    second_contents = client.models.calls[1]["contents"]
    model_content = second_contents[1]
    assert model_content.role == "model"
    assert model_content.parts[0].function_call is not None
    assert model_content.parts[0].function_call.id == "call_preserve"


def test_function_response_content_uses_user_role_and_preserves_id(monkeypatch) -> None:
    monkeypatch.setattr(metadata_tools, "list_tables", lambda: [])
    TOOL_REGISTRY["list_tables"].function = metadata_tools.list_tables
    client = FakeClient([tool_response("list_tables", call_id="call_response"), final_response()])

    generate_pipeline_requirement("Use orders", client=client, settings=settings())

    function_response_content = client.models.calls[1]["contents"][-1]
    assert function_response_content.role == "user"
    function_response = function_response_content.parts[0].function_response
    assert function_response is not None
    assert function_response.name == "list_tables"
    assert function_response.id == "call_response"

def _invalid_requirement_payload() -> dict[str, object]:
    payload = customer_revenue_daily_requirement().model_dump(mode="json")
    payload["transformations"][0]["input_columns"] = ["c.customer_id"]
    return payload


def test_invalid_first_final_response_followed_by_valid_correction() -> None:
    client = FakeClient([final_response(_invalid_requirement_payload(), "invalid_final"), final_response(response_id="corrected_final")])

    result = generate_pipeline_requirement("Create customer revenue", client=client, settings=settings())

    assert result.status == "success"
    assert result.requirement is not None
    assert result.request_id == "corrected_final"
    assert len(client.models.calls) == 2
    correction_contents = client.models.calls[1]["contents"]
    assert correction_contents[1].role == "user"
    correction_text = correction_contents[1].parts[0].text
    assert "failed local Pydantic validation" in correction_text
    assert "join transformations require at least two input_columns" in correction_text


def test_invalid_first_and_invalid_correction_returns_controlled_error() -> None:
    client = FakeClient([
        final_response(_invalid_requirement_payload(), "invalid_final"),
        final_response(_invalid_requirement_payload(), "invalid_correction"),
    ])

    result = generate_pipeline_requirement_result("Create customer revenue", client=client, settings=settings())

    assert result.status == "error"
    assert "Corrected PipelineRequirement is still invalid" in result.message
    assert len(client.models.calls) == 2


def test_prompt_includes_join_semantic_validation_guidance() -> None:
    from agent.prompts import PIPELINE_AGENT_INSTRUCTIONS, REQUIREMENT_CORRECTION_INSTRUCTIONS

    assert "join transformation must include at least two input_columns" in PIPELINE_AGENT_INSTRUCTIONS
    assert "join transformations require at least two input_columns" in REQUIREMENT_CORRECTION_INSTRUCTIONS


def test_prompt_includes_aggregate_semantic_validation_guidance() -> None:
    from agent.prompts import PIPELINE_AGENT_INSTRUCTIONS, REQUIREMENT_CORRECTION_INSTRUCTIONS

    assert "Aggregate, derive, and rename transformations must include output_column" in PIPELINE_AGENT_INSTRUCTIONS
    assert "aggregate, derive, and rename transformations require output_column" in REQUIREMENT_CORRECTION_INSTRUCTIONS


def test_prompt_includes_full_load_deduplication_guidance() -> None:
    from agent.prompts import PIPELINE_AGENT_INSTRUCTIONS, REQUIREMENT_CORRECTION_INSTRUCTIONS

    assert "full load_strategy must not define incremental_column, watermark_column, or deduplication_keys" in PIPELINE_AGENT_INSTRUCTIONS
    assert "full load_strategy cannot include incremental_column, watermark_column, or deduplication_keys" in REQUIREMENT_CORRECTION_INSTRUCTIONS
