"""Final cross-phase hardening tests with mocked provider behavior only."""

from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from google.genai import errors
from agent.pipeline_agent import generate_pipeline_requirement, generate_pipeline_requirement_result
from app.main import app
from config.settings import Settings
from pipeline.examples import customer_revenue_daily_requirement
from pipeline_plan.generator import generate_pipeline_plan
from pipeline_plan.models import PipelinePlan
from pipeline_plan.validator import validate_pipeline_plan
from quality.generator import generate_quality_plan
from quality.models import GeneratedDataQualityPlan
from quality.validator import validate_generated_quality_plan
from sql_generation.generator import SQLProviderError, generate_sql
from sql_generation.models import GeneratedSQL
from sql_generation.validator import validate_generated_sql


class FakeModels:
    def __init__(self, responses: list[object] | None = None, error: Exception | None = None) -> None:
        self._responses = responses or []
        self.error = error
        self.calls: list[dict[str, object]] = []

    def generate_content(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        assert self._responses, "Unexpected Gemini call in Phase 9 integration test"
        return self._responses.pop(0)


class FakeClient:
    def __init__(self, responses: list[object] | None = None, error: Exception | None = None) -> None:
        self.models = FakeModels(responses, error)


def settings() -> Settings:
    return Settings(GEMINI_API_KEY="test-key", GEMINI_MODEL="test-model")


def fake_response(payload: dict[str, object]) -> object:
    return SimpleNamespace(response_id="test-response", function_calls=[], text=json.dumps(payload), parsed=None)


def sql_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "pipeline_name": "customer_revenue_daily",
        "dialect": "postgresql",
        "sql": """
MERGE INTO curated.customer_revenue AS target
USING (
    SELECT c.customer_id, SUM(o.amount) AS total_revenue, MAX(o.order_date) AS last_order_date
    FROM raw.customers AS c
    JOIN raw.orders AS o ON c.customer_id = o.customer_id
    WHERE o.status = 'completed'
    GROUP BY c.customer_id
    HAVING MAX(o.order_date) > :last_successful_watermark
) AS source
ON target.customer_id = source.customer_id
WHEN MATCHED THEN UPDATE SET
    total_revenue = source.total_revenue,
    last_order_date = source.last_order_date
WHEN NOT MATCHED THEN INSERT (customer_id, total_revenue, last_order_date)
VALUES (source.customer_id, source.total_revenue, source.last_order_date);
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
            },
            {
                "rule_name": "check_orders_status_accepted_values",
                "rule_type": "accepted_values",
                "table": {"schema_name": "raw", "table_name": "orders"},
                "column": "status",
                "parameters": {"accepted_values": ["completed"]},
                "severity": "warning",
                "description": "Order status should match the requirement filter.",
            },
        ],
        "validation_status": "valid",
        "warnings": [],
        "validation_errors": [],
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
            ),
            step_payload(
                step_id="load_customer_revenue",
                step_type="load",
                description="Execute merge load operation into curated.customer_revenue",
                depends_on=["transform_customer_revenue"],
                inputs=["transformed_customer_revenue", "generated_sql"],
                outputs=["curated.customer_revenue"],
            ),
            step_payload(
                step_id="validate_quality",
                step_type="quality_validation",
                description="Run quality validation after load",
                depends_on=["load_customer_revenue"],
                inputs=["curated.customer_revenue", "quality_rules"],
                outputs=["quality_validation_outcome"],
            ),
            step_payload(
                step_id="monitor_pipeline",
                step_type="monitoring",
                description="Monitor pipeline completion",
                depends_on=["validate_quality"],
                inputs=["quality_validation_outcome"],
                outputs=["monitoring_summary"],
                failure_behavior="continue_with_warning",
            ),
        ],
        "quality_checks": ["target_customer_id_not_null", "check_orders_status_accepted_values"],
        "observability": {"metrics": ["step_status", "record_counts"], "logs": ["failure_reason"]},
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
        "description": "Perform source validation.",
        "depends_on": [],
        "inputs": ["raw.customers", "raw.orders"],
        "outputs": ["source_metadata"],
        "retry_policy": {"max_attempts": 1, "delay_seconds": 0, "backoff_strategy": "none"},
        "failure_behavior": "fail_pipeline",
    }
    payload.update(overrides)
    return payload


def test_mocked_customer_revenue_end_to_end_artifact_flow() -> None:
    requirement_response = fake_response(customer_revenue_daily_requirement().model_dump(mode="json"))
    requirement_result = generate_pipeline_requirement(
        "Create a daily customer revenue pipeline.",
        client=FakeClient([requirement_response]),
        settings=settings(),
    )
    assert requirement_result.status == "success"
    requirement = requirement_result.requirement
    assert requirement is not None

    sql = generate_sql(requirement, client=FakeClient([fake_response(sql_payload())]), settings=settings())
    quality = generate_quality_plan(requirement, client=FakeClient([fake_response(quality_payload())]), settings=settings())
    plan = generate_pipeline_plan(requirement, sql, quality, client=FakeClient([fake_response(plan_payload())]), settings=settings())

    assert sql.validation_status == "valid"
    assert quality.validation_status == "valid"
    assert plan.validation_status == "valid"
    assert plan.pipeline_name == requirement.pipeline_name == sql.pipeline_name == quality.pipeline_name
    assert sql.source_tables == [source.table.qualified_name for source in requirement.sources]
    assert sql.target_table == requirement.target.table.qualified_name
    assert plan.schedule.frequency == requirement.schedule.frequency


def test_cross_phase_artifacts_remain_mutually_consistent() -> None:
    requirement = customer_revenue_daily_requirement()
    sql = GeneratedSQL.model_validate(sql_payload())
    quality = GeneratedDataQualityPlan.model_validate(quality_payload())
    quality = validate_generated_quality_plan(requirement, quality)
    pipeline_plan = PipelinePlan.model_validate(plan_payload())

    validated_sql = validate_generated_sql(requirement, sql)
    validated_plan = validate_pipeline_plan(requirement, validated_sql, quality, pipeline_plan)

    assert validated_sql.validation_status == "valid"
    assert quality.validation_status == "valid"
    assert validated_plan.validation_status == "valid"


def test_sql_artifact_cannot_introduce_unrelated_tables() -> None:
    requirement = customer_revenue_daily_requirement()
    payload = sql_payload(
        sql=str(sql_payload()["sql"]).replace("raw.orders", "raw.payments"),
        source_tables=["raw.customers", "raw.payments"],
    )

    result = validate_generated_sql(requirement, GeneratedSQL.model_validate(payload))

    assert result.validation_status == "invalid"
    assert any("unrelated tables" in error for error in result.validation_errors)


def test_quality_plan_cannot_introduce_unrelated_tables() -> None:
    requirement = customer_revenue_daily_requirement()
    payload = quality_payload(
        rules=[
            {
                "rule_name": "payments_amount_positive",
                "rule_type": "positive_value",
                "table": {"schema_name": "raw", "table_name": "payments"},
                "column": "amount",
                "parameters": {},
                "severity": "error",
                "description": "Payment amount should be positive.",
            }
        ]
    )

    result = validate_generated_quality_plan(requirement, GeneratedDataQualityPlan.model_validate(payload))

    assert result.validation_status == "invalid"
    assert any("unrelated table" in error for error in result.validation_errors)


def test_pipeline_plan_cannot_introduce_unrelated_tables_or_unknown_quality_checks() -> None:
    requirement = customer_revenue_daily_requirement()
    sql = validate_generated_sql(requirement, GeneratedSQL.model_validate(sql_payload()))
    quality = validate_generated_quality_plan(requirement, GeneratedDataQualityPlan.model_validate(quality_payload()))
    steps = plan_payload()["execution_steps"]
    steps[1] = {**steps[1], "outputs": ["raw.payments"]}
    pipeline_plan = PipelinePlan.model_validate(plan_payload(execution_steps=steps, quality_checks=["missing_quality_rule"]))

    result = validate_pipeline_plan(requirement, sql, quality, pipeline_plan)

    assert result.validation_status == "invalid"
    assert any("unrelated table" in error for error in result.validation_errors)
    assert any("unknown quality checks" in error for error in result.validation_errors)


def test_invalid_sql_artifact_prevents_downstream_planning() -> None:
    requirement = customer_revenue_daily_requirement()
    invalid_sql = validate_generated_sql(requirement, GeneratedSQL.model_validate(sql_payload(sql="DROP TABLE raw.orders;")))
    quality = validate_generated_quality_plan(requirement, GeneratedDataQualityPlan.model_validate(quality_payload()))
    pipeline_plan = PipelinePlan.model_validate(plan_payload())

    result = validate_pipeline_plan(requirement, invalid_sql, quality, pipeline_plan)

    assert result.validation_status == "invalid"
    assert any("GeneratedSQL must be locally valid" in error for error in result.validation_errors)


def test_quality_plan_semantic_failure_prevents_downstream_planning() -> None:
    requirement = customer_revenue_daily_requirement()
    sql = validate_generated_sql(requirement, GeneratedSQL.model_validate(sql_payload()))
    invalid_quality = validate_generated_quality_plan(
        requirement,
        GeneratedDataQualityPlan.model_validate(quality_payload(pipeline_name="other_pipeline")),
    )
    pipeline_plan = PipelinePlan.model_validate(plan_payload())

    result = validate_pipeline_plan(requirement, sql, invalid_quality, pipeline_plan)

    assert result.validation_status == "invalid"
    assert any("GeneratedDataQualityPlan must be locally valid" in error for error in result.validation_errors)


def test_cyclic_pipeline_plan_is_rejected_in_cross_phase_validation() -> None:
    requirement = customer_revenue_daily_requirement()
    sql = validate_generated_sql(requirement, GeneratedSQL.model_validate(sql_payload()))
    quality = validate_generated_quality_plan(requirement, GeneratedDataQualityPlan.model_validate(quality_payload()))
    steps = plan_payload()["execution_steps"]
    steps[1] = {**steps[1], "depends_on": ["transform_customer_revenue"]}
    steps[2] = {**steps[2], "depends_on": ["extract_sources"]}

    result = validate_pipeline_plan(requirement, sql, quality, PipelinePlan.model_validate(plan_payload(execution_steps=steps)))

    assert result.validation_status == "invalid"
    assert any("circular dependency" in error for error in result.validation_errors)


def test_malformed_requirement_provider_output_returns_controlled_agent_error() -> None:
    result = generate_pipeline_requirement_result(
        "Create customer revenue",
        client=FakeClient([SimpleNamespace(response_id="bad", function_calls=[], text="{not-json", parsed=None)]),
        settings=settings(),
    )

    assert result.status == "error"
    assert "invalid structured JSON" in result.message


def test_gemini_quota_and_service_failures_are_mapped_for_http_routes(monkeypatch) -> None:
    quota_error = SQLProviderError("Gemini SQL generation quota exceeded", http_status=429)
    service_error = SQLProviderError("Gemini SQL generation temporarily unavailable", http_status=503)

    monkeypatch.setattr("app.sql.generate_sql", lambda requirement: (_ for _ in ()).throw(quota_error))
    client = TestClient(app)
    quota_response = client.post("/sql/generate", json=customer_revenue_daily_requirement().model_dump(mode="json"))

    monkeypatch.setattr("app.sql.generate_sql", lambda requirement: (_ for _ in ()).throw(service_error))
    service_response = client.post("/sql/generate", json=customer_revenue_daily_requirement().model_dump(mode="json"))

    assert quota_response.status_code == 429
    assert service_response.status_code == 503


def test_provider_error_logging_does_not_dump_secret_like_messages(caplog) -> None:
    secret_like_message = "GEMINI_API_KEY=should-not-appear"
    error = errors.ServerError(503, {"error": {"message": secret_like_message}})

    with caplog.at_level("WARNING"):
        try:
            generate_sql(
                customer_revenue_daily_requirement(),
                client=FakeClient(error=error),
                settings=settings(),
            )
        except SQLProviderError:
            pass

    assert "Gemini SQL generation failed" in caplog.text
    assert secret_like_message not in caplog.text
