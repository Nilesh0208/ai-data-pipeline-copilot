"""Tests for Phase 7 data-quality generation without live Gemini calls or rule execution."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from google.genai import errors, types

from app.main import app
from pipeline.examples import customer_revenue_daily_requirement
from pipeline.requirements import ScheduleDefinition
from quality.generator import QualityStructuredOutputError, generate_quality_plan, normalize_quality_plan
from quality.models import GeneratedDataQualityPlan, QualityValidationStatus
from quality.validator import validate_generated_quality_plan


class FakeModels:
    def __init__(self, responses: list[object] | None = None, error: Exception | None = None) -> None:
        self._responses = responses or []
        self.error = error
        self.calls: list[dict[str, object]] = []

    def generate_content(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        assert self._responses, "Unexpected Gemini quality generation call"
        return self._responses.pop(0)


class FakeClient:
    def __init__(self, responses: list[object] | None = None, error: Exception | None = None) -> None:
        self.models = FakeModels(responses, error)


def metadata_catalog() -> dict[str, dict[str, str]]:
    return {
        "raw.customers": {
            "customer_id": "BIGINT",
            "customer_name": "VARCHAR",
            "email": "VARCHAR",
            "country": "VARCHAR",
            "created_at": "TIMESTAMP",
            "updated_at": "TIMESTAMP",
        },
        "raw.orders": {
            "order_id": "BIGINT",
            "customer_id": "BIGINT",
            "order_date": "TIMESTAMP",
            "status": "VARCHAR",
            "amount": "NUMERIC(12,2)",
            "currency": "VARCHAR(3)",
            "created_at": "TIMESTAMP",
            "updated_at": "TIMESTAMP",
        },
        "curated.customer_revenue": {
            "customer_id": "BIGINT",
            "customer_name": "VARCHAR",
            "total_orders": "BIGINT",
            "total_revenue": "NUMERIC(14,2)",
            "last_order_date": "TIMESTAMP",
            "updated_at": "TIMESTAMP",
        },
    }


def rule_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "rule_name": "orders_amount_positive",
        "rule_type": "positive_value",
        "table": {"schema_name": "raw", "table_name": "orders"},
        "column": "amount",
        "parameters": {},
        "severity": "error",
        "description": "Order amount should be positive.",
    }
    payload.update(overrides)
    return payload


def valid_quality_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "pipeline_name": "customer_revenue_daily",
        "rules": [
            rule_payload(
                rule_name="orders_customer_id_not_null",
                rule_type="not_null",
                column="customer_id",
                description="Order customer identifier should be present.",
            ),
            rule_payload(
                rule_name="orders_amount_positive",
                rule_type="positive_value",
                column="amount",
                description="Order amount should be positive.",
            ),
            rule_payload(
                rule_name="target_customer_id_not_null",
                rule_type="not_null",
                table={"schema_name": "curated", "table_name": "customer_revenue"},
                column="customer_id",
                description="Target customer identifier should be present.",
            ),
        ],
        "validation_status": "valid",
        "warnings": [],
        "validation_errors": [],
    }
    payload.update(overrides)
    return payload


def fake_response(payload: dict[str, object]) -> object:
    return SimpleNamespace(text=json.dumps(payload), parsed=None)


def plan_from_payload(payload: dict[str, object]) -> GeneratedDataQualityPlan:
    return GeneratedDataQualityPlan.model_validate(payload)


def validate_payload(payload: dict[str, object]) -> GeneratedDataQualityPlan:
    return validate_generated_quality_plan(
        customer_revenue_daily_requirement(),
        plan_from_payload(payload),
        metadata_catalog=metadata_catalog(),
    )


def normalize_and_validate_payload(
    payload: dict[str, object],
    *,
    requirement=None,
    metadata=None,
) -> GeneratedDataQualityPlan:
    active_requirement = requirement or customer_revenue_daily_requirement()
    normalized = normalize_quality_plan(active_requirement, plan_from_payload(payload))
    return validate_generated_quality_plan(active_requirement, normalized, metadata_catalog=metadata)


def requirement_with_filter_expression(expression: dict[str, object]):
    requirement = customer_revenue_daily_requirement()
    transformations = [
        transformation.model_copy(update={"expression": expression, "input_columns": ["o.status"]})
        if transformation.rule_type == "filter"
        else transformation
        for transformation in requirement.transformations
    ]
    return requirement.model_copy(update={"transformations": transformations})


def accepted_values_status_payload(parameters: dict[str, object] | None = None) -> dict[str, object]:
    return valid_quality_payload(
        rules=[
            rule_payload(
                rule_name="check_orders_status_accepted_values",
                rule_type="accepted_values",
                column="status",
                parameters=parameters or {},
                description="Order status should match the explicit requirement filter.",
            )
        ]
    )


def test_valid_quality_plan_generation() -> None:
    requirement = customer_revenue_daily_requirement()
    client = FakeClient([fake_response(valid_quality_payload())])

    result = generate_quality_plan(requirement, client=client, metadata_catalog=metadata_catalog())

    assert result.validation_status == QualityValidationStatus.VALID
    assert len(result.rules) == 3
    assert client.models.calls[0]["model"]
    assert isinstance(client.models.calls[0]["config"], types.GenerateContentConfig)


def test_pipeline_name_preservation() -> None:
    result = validate_payload(valid_quality_payload(pipeline_name="other_pipeline"))

    assert result.validation_status == "invalid"
    assert any("pipeline_name does not match" in error for error in result.validation_errors)


def test_allowed_source_table_reference() -> None:
    result = validate_payload(valid_quality_payload(rules=[rule_payload(table={"schema_name": "raw", "table_name": "orders"})]))

    assert result.validation_status == "valid"


def test_allowed_target_table_reference() -> None:
    result = validate_payload(
        valid_quality_payload(
            rules=[
                rule_payload(
                    table={"schema_name": "curated", "table_name": "customer_revenue"},
                    column="total_revenue",
                )
            ]
        )
    )

    assert result.validation_status == "valid"


def test_unrelated_table_rejection() -> None:
    result = validate_payload(valid_quality_payload(rules=[rule_payload(table={"schema_name": "raw", "table_name": "payments"})]))

    assert result.validation_status == "invalid"
    assert any("unrelated table" in error for error in result.validation_errors)


def test_invalid_column_rejection_when_metadata_proves_missing_column() -> None:
    result = validate_payload(valid_quality_payload(rules=[rule_payload(column="missing_column")]))

    assert result.validation_status == "invalid"
    assert any("unknown column" in error for error in result.validation_errors)


def test_not_null_without_column_rejection() -> None:
    result = validate_payload(valid_quality_payload(rules=[rule_payload(rule_type="not_null", column=None)]))

    assert result.validation_status == "invalid"
    assert any("not_null" in error and "requires column" in error for error in result.validation_errors)


def test_accepted_values_without_values_rejection() -> None:
    result = validate_payload(valid_quality_payload(rules=[rule_payload(rule_type="accepted_values", column="status")]))

    assert result.validation_status == "invalid"
    assert any("non-empty accepted_values" in error for error in result.validation_errors)


def test_accepted_values_with_structured_values_remains_valid() -> None:
    result = validate_payload(
        valid_quality_payload(
            rules=[
                rule_payload(
                    rule_type="accepted_values",
                    column="status",
                    parameters={"accepted_values": ["completed"]},
                    description="Order status should match requirement filter values.",
                )
            ]
        )
    )

    assert result.validation_status == "valid"


def test_range_with_invalid_parameters_rejection() -> None:
    result = validate_payload(
        valid_quality_payload(rules=[rule_payload(rule_type="range", column="amount", parameters={"min": 100, "max": 10})])
    )

    assert result.validation_status == "invalid"
    assert any("min must be less than or equal to max" in error for error in result.validation_errors)


def test_range_with_structured_numeric_bounds_remains_valid() -> None:
    result = validate_payload(
        valid_quality_payload(
            rules=[
                rule_payload(
                    rule_type="range",
                    column="amount",
                    parameters={"min": 0},
                    description="Order amount should not be negative.",
                )
            ]
        )
    )

    assert result.validation_status == "valid"


def test_referential_integrity_with_structured_reference_parameter() -> None:
    result = validate_payload(
        valid_quality_payload(
            rules=[
                rule_payload(
                    rule_name="raw_orders_customer_id_fk",
                    rule_type="referential_integrity",
                    column="customer_id",
                    parameters={
                        "reference": {
                            "table": {"schema_name": "raw", "table_name": "customers"},
                            "column": "customer_id",
                        }
                    },
                    description="Order customer identifiers should reference pipeline customer records.",
                )
            ]
        )
    )

    assert result.validation_status == "valid"


def test_referential_integrity_missing_reference_parameter_is_invalid() -> None:
    result = validate_payload(
        valid_quality_payload(
            rules=[
                rule_payload(
                    rule_name="raw_orders_customer_id_fk",
                    rule_type="referential_integrity",
                    column="customer_id",
                    parameters={},
                    description="Order customer identifiers should reference customer records.",
                )
            ]
        )
    )

    assert result.validation_status == "invalid"
    assert any("requires structured reference parameter" in error for error in result.validation_errors)


def test_referential_integrity_reference_table_must_belong_to_requirement() -> None:
    result = validate_payload(
        valid_quality_payload(
            rules=[
                rule_payload(
                    rule_name="raw_orders_customer_id_fk",
                    rule_type="referential_integrity",
                    column="customer_id",
                    parameters={
                        "reference": {
                            "table": {"schema_name": "raw", "table_name": "payments"},
                            "column": "customer_id",
                        }
                    },
                    description="Order customer identifiers should reference customer records.",
                )
            ]
        )
    )

    assert result.validation_status == "invalid"
    assert any("unrelated reference.table" in error for error in result.validation_errors)


def test_freshness_with_valid_structured_schedule_threshold() -> None:
    result = validate_payload(
        valid_quality_payload(
            rules=[
                rule_payload(
                    rule_name="curated_customer_revenue_freshness",
                    rule_type="freshness",
                    table={"schema_name": "curated", "table_name": "customer_revenue"},
                    column="updated_at",
                    parameters={"threshold": {"value": 2, "unit": "days"}},
                    description="Daily target refresh should remain within the conservative schedule threshold.",
                )
            ]
        )
    )

    assert result.validation_status == "valid"


def test_freshness_without_justified_threshold_is_invalid() -> None:
    result = validate_payload(
        valid_quality_payload(
            rules=[
                rule_payload(
                    rule_name="curated_customer_revenue_freshness",
                    rule_type="freshness",
                    table={"schema_name": "curated", "table_name": "customer_revenue"},
                    column="updated_at",
                    parameters={},
                    description="Target refresh should be recent.",
                )
            ]
        )
    )

    assert result.validation_status == "invalid"
    assert any("requires structured threshold parameter" in error for error in result.validation_errors)


def test_freshness_rejects_fabricated_arbitrary_threshold() -> None:
    result = validate_payload(
        valid_quality_payload(
            rules=[
                rule_payload(
                    rule_name="curated_customer_revenue_freshness",
                    rule_type="freshness",
                    table={"schema_name": "curated", "table_name": "customer_revenue"},
                    column="updated_at",
                    parameters={"threshold": {"value": 7, "unit": "days"}},
                    description="Target refresh should meet an arbitrary weekly SLA.",
                )
            ]
        )
    )

    assert result.validation_status == "invalid"
    assert any("not justified by PipelineRequirement schedule" in error for error in result.validation_errors)


def test_freshness_parameter_normalized_from_daily_schedule() -> None:
    result = normalize_and_validate_payload(
        valid_quality_payload(
            rules=[
                rule_payload(
                    rule_name="curated_customer_revenue_freshness",
                    rule_type="freshness",
                    table={"schema_name": "curated", "table_name": "customer_revenue"},
                    column="updated_at",
                    parameters={},
                    description="Target refresh should be recent.",
                )
            ]
        ),
        metadata=metadata_catalog(),
    )

    assert result.validation_status == "valid"
    assert result.rules[0].parameters == {"threshold": {"value": 2, "unit": "days"}}


def test_freshness_manual_schedule_not_fabricated() -> None:
    requirement = customer_revenue_daily_requirement().model_copy(
        update={"schedule": ScheduleDefinition(frequency="manual", timezone="UTC", enabled=False)}
    )

    result = normalize_and_validate_payload(
        valid_quality_payload(
            rules=[
                rule_payload(
                    rule_name="curated_customer_revenue_freshness",
                    rule_type="freshness",
                    table={"schema_name": "curated", "table_name": "customer_revenue"},
                    column="updated_at",
                    parameters={},
                    description="Manual target refresh should not receive a fabricated threshold.",
                )
            ]
        ),
        requirement=requirement,
        metadata=metadata_catalog(),
    )

    assert result.validation_status == "invalid"
    assert result.rules[0].parameters == {}
    assert any("requires structured threshold parameter" in error for error in result.validation_errors)


def test_referential_integrity_reference_derived_from_explicit_join_relationship() -> None:
    result = normalize_and_validate_payload(
        valid_quality_payload(
            rules=[
                rule_payload(
                    rule_name="raw_orders_customer_id_fk",
                    rule_type="referential_integrity",
                    column="customer_id",
                    parameters={},
                    description="Order customer identifiers should reference customer records.",
                )
            ]
        ),
        metadata=metadata_catalog(),
    )

    assert result.validation_status == "valid"
    assert result.rules[0].parameters == {
        "reference": {
            "table": {"schema_name": "raw", "table_name": "customers"},
            "column": "customer_id",
        }
    }


def test_unrelated_fk_not_inferred() -> None:
    result = normalize_and_validate_payload(
        valid_quality_payload(
            rules=[
                rule_payload(
                    rule_name="raw_orders_currency_fk",
                    rule_type="referential_integrity",
                    column="currency",
                    parameters={},
                    description="Currency should not receive an inferred customer reference.",
                )
            ]
        ),
        metadata=metadata_catalog(),
    )

    assert result.validation_status == "invalid"
    assert result.rules[0].parameters == {}
    assert any("requires structured reference parameter" in error for error in result.validation_errors)


def test_accepted_values_derived_from_explicit_filter_value() -> None:
    result = normalize_and_validate_payload(
        valid_quality_payload(
            rules=[
                rule_payload(
                    rule_name="raw_orders_status_values",
                    rule_type="accepted_values",
                    column="status",
                    parameters={},
                    description="Order status should match the requirement filter.",
                )
            ]
        ),
        metadata=metadata_catalog(),
    )

    assert result.validation_status == "valid"
    assert result.rules[0].parameters == {"accepted_values": ["completed"]}


def test_accepted_values_derived_from_unquoted_equality_literal() -> None:
    result = normalize_and_validate_payload(
        accepted_values_status_payload(),
        requirement=requirement_with_filter_expression({"condition": "o.status = completed"}),
        metadata=metadata_catalog(),
    )

    assert result.validation_status == "valid"
    assert result.rules[0].parameters == {"accepted_values": ["completed"]}


def test_accepted_values_derived_from_single_quoted_equality_literal() -> None:
    result = normalize_and_validate_payload(
        accepted_values_status_payload(),
        requirement=requirement_with_filter_expression({"condition": "o.status = 'completed'"}),
        metadata=metadata_catalog(),
    )

    assert result.validation_status == "valid"
    assert result.rules[0].parameters == {"accepted_values": ["completed"]}


def test_accepted_values_derived_from_double_quoted_equality_literal() -> None:
    result = normalize_and_validate_payload(
        accepted_values_status_payload(),
        requirement=requirement_with_filter_expression({"condition": 'o.status = "completed"'}),
        metadata=metadata_catalog(),
    )

    assert result.validation_status == "valid"
    assert result.rules[0].parameters == {"accepted_values": ["completed"]}


def test_wrong_column_does_not_enrich_accepted_values() -> None:
    result = normalize_and_validate_payload(
        accepted_values_status_payload(),
        requirement=requirement_with_filter_expression({"condition": "o.currency = usd"}),
        metadata=metadata_catalog(),
    )

    assert result.validation_status == "invalid"
    assert result.rules[0].parameters == {}
    assert any("requires non-empty accepted_values" in error for error in result.validation_errors)


def test_non_equality_expression_does_not_create_accepted_values() -> None:
    result = normalize_and_validate_payload(
        accepted_values_status_payload(),
        requirement=requirement_with_filter_expression({"condition": "o.status != completed"}),
        metadata=metadata_catalog(),
    )

    assert result.validation_status == "invalid"
    assert result.rules[0].parameters == {}
    assert any("requires non-empty accepted_values" in error for error in result.validation_errors)


def test_accepted_values_normalization_does_not_invent_additional_values() -> None:
    result = normalize_and_validate_payload(
        accepted_values_status_payload(),
        requirement=requirement_with_filter_expression({"condition": "o.status = completed"}),
        metadata=metadata_catalog(),
    )

    assert result.rules[0].parameters["accepted_values"] == ["completed"]
    assert "pending" not in result.rules[0].parameters["accepted_values"]
    assert "cancelled" not in result.rules[0].parameters["accepted_values"]
    assert "failed" not in result.rules[0].parameters["accepted_values"]
    assert "processing" not in result.rules[0].parameters["accepted_values"]


def test_existing_valid_accepted_values_parameter_preserved() -> None:
    result = normalize_and_validate_payload(
        accepted_values_status_payload({"accepted_values": ["completed"]}),
        requirement=requirement_with_filter_expression({"condition": "o.status = completed"}),
        metadata=metadata_catalog(),
    )

    assert result.validation_status == "valid"
    assert result.rules[0].parameters == {"accepted_values": ["completed"]}


def test_conflicting_accepted_values_preserved_and_rejected() -> None:
    result = normalize_and_validate_payload(
        accepted_values_status_payload({"accepted_values": ["pending"]}),
        requirement=requirement_with_filter_expression({"condition": "o.status = completed"}),
        metadata=metadata_catalog(),
    )

    assert result.validation_status == "invalid"
    assert result.rules[0].parameters == {"accepted_values": ["pending"]}
    assert any("values are not justified" in error for error in result.validation_errors)


def test_accepted_values_not_fabricated_without_explicit_values() -> None:
    result = normalize_and_validate_payload(
        valid_quality_payload(
            rules=[
                rule_payload(
                    rule_name="raw_orders_currency_values",
                    rule_type="accepted_values",
                    column="currency",
                    parameters={},
                    description="Currency values are not explicitly enumerated by the requirement.",
                )
            ]
        ),
        metadata=metadata_catalog(),
    )

    assert result.validation_status == "invalid"
    assert result.rules[0].parameters == {}
    assert any("requires non-empty accepted_values" in error for error in result.validation_errors)


def test_valid_existing_parameters_preserved() -> None:
    plan = normalize_quality_plan(
        customer_revenue_daily_requirement(),
        plan_from_payload(
            valid_quality_payload(
                rules=[
                    rule_payload(
                        rule_name="curated_customer_revenue_freshness",
                        rule_type="freshness",
                        table={"schema_name": "curated", "table_name": "customer_revenue"},
                        column="updated_at",
                        parameters={"threshold": {"value": 2, "unit": "days"}},
                        description="Daily target refresh should remain within the conservative schedule threshold.",
                    )
                ]
            )
        ),
    )

    assert plan.rules[0].parameters == {"threshold": {"value": 2, "unit": "days"}}


def test_conflicting_parameters_rejected_not_silently_rewritten() -> None:
    result = normalize_and_validate_payload(
        valid_quality_payload(
            rules=[
                rule_payload(
                    rule_name="curated_customer_revenue_freshness",
                    rule_type="freshness",
                    table={"schema_name": "curated", "table_name": "customer_revenue"},
                    column="updated_at",
                    parameters={"threshold": {"value": 7, "unit": "days"}},
                    description="Target refresh should not keep an arbitrary threshold.",
                )
            ]
        ),
        metadata=metadata_catalog(),
    )

    assert result.validation_status == "invalid"
    assert result.rules[0].parameters == {"threshold": {"value": 7, "unit": "days"}}
    assert any("not justified by PipelineRequirement schedule" in error for error in result.validation_errors)


def test_current_customer_revenue_daily_scenario_valid_after_normalization() -> None:
    client = FakeClient(
        [
            fake_response(
                valid_quality_payload(
                    rules=[
                        rule_payload(
                            rule_name="curated_customer_revenue_freshness",
                            rule_type="freshness",
                            table={"schema_name": "curated", "table_name": "customer_revenue"},
                            column="updated_at",
                            parameters={},
                            description="Target refresh should be recent.",
                        ),
                        rule_payload(
                            rule_name="raw_orders_customer_id_fk",
                            rule_type="referential_integrity",
                            column="customer_id",
                            parameters={},
                            description="Order customer identifiers should reference customer records.",
                        ),
                        rule_payload(
                            rule_name="raw_orders_status_values",
                            rule_type="accepted_values",
                            column="status",
                            parameters={},
                            description="Order status should match the requirement filter.",
                        ),
                    ]
                )
            )
        ]
    )

    result = generate_quality_plan(customer_revenue_daily_requirement(), client=client, metadata_catalog=metadata_catalog())

    assert result.validation_status == "valid"
    assert len(client.models.calls) == 1


def test_row_count_as_valid_table_level_rule() -> None:
    result = validate_payload(
        valid_quality_payload(
            rules=[
                rule_payload(
                    rule_name="orders_row_count_positive",
                    rule_type="row_count",
                    column=None,
                    parameters={"min": 1},
                    description="Orders should have records.",
                )
            ]
        )
    )

    assert result.validation_status == "valid"


def test_duplicate_rule_rejection() -> None:
    duplicate = rule_payload(rule_name="orders_amount_positive")
    result = validate_payload(valid_quality_payload(rules=[duplicate, {**duplicate, "rule_name": "orders_amount_positive_copy"}]))

    assert result.validation_status == "invalid"
    assert any("duplicate quality rule" in error for error in result.validation_errors)


def test_unsupported_rule_type_rejection() -> None:
    with pytest.raises(QualityStructuredOutputError, match="invalid GeneratedDataQualityPlan"):
        generate_quality_plan(
            customer_revenue_daily_requirement(),
            client=FakeClient([fake_response(valid_quality_payload(rules=[rule_payload(rule_type="regex_match")]))]),
        )


def test_invalid_severity_rejection() -> None:
    with pytest.raises(QualityStructuredOutputError, match="invalid GeneratedDataQualityPlan"):
        generate_quality_plan(
            customer_revenue_daily_requirement(),
            client=FakeClient([fake_response(valid_quality_payload(rules=[rule_payload(severity="fatal")]))]),
        )


def test_malformed_gemini_response() -> None:
    client = FakeClient([SimpleNamespace(text="{not valid json", parsed=None)])

    with pytest.raises(QualityStructuredOutputError, match="malformed generated quality-plan JSON"):
        generate_quality_plan(customer_revenue_daily_requirement(), client=client)


def test_gemini_client_failure() -> None:
    error = errors.ClientError(429, {"error": {"message": "quota exceeded"}})

    with pytest.raises(Exception, match="Gemini quality generation failed"):
        generate_quality_plan(customer_revenue_daily_requirement(), client=FakeClient(error=error))


def test_api_success_with_mocked_gemini(monkeypatch) -> None:
    expected = plan_from_payload(valid_quality_payload())

    monkeypatch.setattr("app.quality.generate_quality_plan", lambda requirement: expected)
    client = TestClient(app)

    response = client.post("/quality/generate", json=customer_revenue_daily_requirement().model_dump(mode="json"))

    assert response.status_code == 200
    assert response.json()["pipeline_name"] == "customer_revenue_daily"
    assert response.json()["validation_status"] == "valid"


def test_api_controlled_failure(monkeypatch) -> None:
    from quality.generator import QualityGenerationError

    def fail(requirement: object) -> None:
        raise QualityGenerationError("Gemini quality generation failed")

    monkeypatch.setattr("app.quality.generate_quality_plan", fail)
    client = TestClient(app)

    response = client.post("/quality/generate", json=customer_revenue_daily_requirement().model_dump(mode="json"))

    assert response.status_code == 503
    assert response.json()["detail"] == "Gemini quality generation failed"


def test_quality_generation_does_not_execute_sql_or_request_database_engine(monkeypatch) -> None:
    def fail_if_database_engine_requested() -> None:
        raise AssertionError("quality generation must not request a database engine")

    monkeypatch.setattr("database.connection.get_engine", fail_if_database_engine_requested)

    result = generate_quality_plan(customer_revenue_daily_requirement(), client=FakeClient([fake_response(valid_quality_payload())]))

    assert result.validation_status == "valid_with_warnings"


def test_invalid_first_quality_plan_followed_by_valid_correction() -> None:
    invalid_payload = valid_quality_payload(
        rules=[
            rule_payload(
                rule_name="raw_payments_amount_positive",
                table={"schema_name": "raw", "table_name": "payments"},
                column="amount",
                parameters={},
                description="Payments are not part of this PipelineRequirement.",
            )
        ]
    )
    corrected_payload = valid_quality_payload(
        rules=[
            rule_payload(
                rule_name="raw_orders_customer_id_fk",
                rule_type="referential_integrity",
                column="customer_id",
                parameters={
                    "reference": {
                        "table": {"schema_name": "raw", "table_name": "customers"},
                        "column": "customer_id",
                    }
                },
                description="Order customer identifiers should reference pipeline customer records.",
            )
        ]
    )
    client = FakeClient([fake_response(invalid_payload), fake_response(corrected_payload)])

    result = generate_quality_plan(customer_revenue_daily_requirement(), client=client, metadata_catalog=metadata_catalog())

    assert result.validation_status == "valid"
    assert len(client.models.calls) == 2
    correction_contents = client.models.calls[1]["contents"]
    correction_text = correction_contents[-1].parts[0].text
    assert "failed local deterministic semantic validation" in correction_text
    assert "references unrelated table" in correction_text


def test_invalid_first_quality_plan_and_invalid_correction_returns_invalid_result() -> None:
    invalid_payload = valid_quality_payload(
        rules=[
            rule_payload(
                rule_name="orders_amount_invalid_range",
                rule_type="range",
                column="amount",
                parameters={"min": 100, "max": 10},
                description="Order amount has an invalid range.",
            )
        ]
    )
    invalid_correction = valid_quality_payload(
        rules=[
            rule_payload(
                rule_name="orders_amount_invalid_range",
                rule_type="range",
                column="amount",
                parameters={"min": 100, "max": 10},
                description="Order amount still has an invalid range.",
            )
        ]
    )
    client = FakeClient([fake_response(invalid_payload), fake_response(invalid_correction)])

    result = generate_quality_plan(customer_revenue_daily_requirement(), client=client, metadata_catalog=metadata_catalog())

    assert result.validation_status == "invalid"
    assert len(client.models.calls) == 2
    assert any("min must be less than or equal to max" in error for error in result.validation_errors)


def test_existing_invalid_missing_parameters_remain_invalid_when_not_derivable() -> None:
    result = normalize_and_validate_payload(
        valid_quality_payload(
            rules=[
                rule_payload(
                    rule_name="raw_orders_currency_values",
                    rule_type="accepted_values",
                    column="currency",
                    parameters={},
                    description="Currency accepted values are not explicitly present in the requirement.",
                ),
                rule_payload(
                    rule_name="raw_orders_currency_fk",
                    rule_type="referential_integrity",
                    column="currency",
                    parameters={},
                    description="Currency foreign key is not present in explicit join relationships.",
                ),
                rule_payload(
                    rule_name="orders_amount_invalid_range",
                    rule_type="range",
                    column="amount",
                    parameters={"min": 100, "max": 10},
                    description="Order amount has an invalid range.",
                ),
            ]
        ),
        metadata=metadata_catalog(),
    )

    assert result.validation_status == "invalid"
    assert any("requires non-empty accepted_values" in error for error in result.validation_errors)
    assert any("requires structured reference parameter" in error for error in result.validation_errors)
    assert any("min must be less than or equal to max" in error for error in result.validation_errors)


def test_metadata_unavailable_warnings_remain_preserved_after_local_validation() -> None:
    result = generate_quality_plan(customer_revenue_daily_requirement(), client=FakeClient([fake_response(valid_quality_payload())]))

    assert result.validation_status == "valid_with_warnings"
    assert any("metadata unavailable" in warning for warning in result.warnings)


def test_executable_payload_content_rejection() -> None:
    with pytest.raises(QualityStructuredOutputError, match="executable SQL, Python, or shell payloads"):
        generate_quality_plan(
            customer_revenue_daily_requirement(),
            client=FakeClient(
                [
                    fake_response(
                        valid_quality_payload(
                            rules=[
                                rule_payload(
                                    parameters={"accepted_values": ["COMPLETED; DROP TABLE raw.orders"]},
                                    rule_type="accepted_values",
                                    column="status",
                                )
                            ]
                        )
                    )
                ]
            ),
        )
