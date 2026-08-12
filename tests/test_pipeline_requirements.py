"""Tests for the Phase 4 pipeline requirement contract."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from pipeline.examples import customer_revenue_daily_requirement
from pipeline.requirements import (
    LoadStrategy,
    PipelineRequirement,
    PipelineSource,
    PipelineTarget,
    ScheduleDefinition,
    TableReference,
    TransformationRule,
)


def make_valid_requirement() -> PipelineRequirement:
    return PipelineRequirement(
        pipeline_name="Customer_Revenue_Daily",
        description="Daily revenue by customer.",
        sources=[
            PipelineSource(table=TableReference(schema_name="RAW", table_name="Customers"), alias="c"),
            PipelineSource(table=TableReference(schema_name="raw", table_name="orders"), alias="o"),
        ],
        target=PipelineTarget(
            table=TableReference(schema_name="curated", table_name="customer_revenue"),
            write_mode="merge",
        ),
        transformations=[
            TransformationRule(
                rule_type="join",
                description="Join customers to orders.",
                input_columns=["c.customer_id", "o.customer_id"],
                expression={"left_column": "c.customer_id", "right_column": "o.customer_id"},
            )
        ],
        load_strategy=LoadStrategy(
            load_type="incremental",
            watermark_column="last_order_date",
            deduplication_keys=["customer_id"],
        ),
        schedule=ScheduleDefinition(frequency="daily", timezone="UTC", enabled=True),
        tags=["Revenue"],
    )


def test_valid_requirement_normalizes_identifiers() -> None:
    requirement = make_valid_requirement()

    assert requirement.pipeline_name == "customer_revenue_daily"
    assert requirement.sources[0].table.schema_name == "raw"
    assert requirement.sources[0].table.table_name == "customers"
    assert requirement.tags == ["revenue"]


def test_invalid_pipeline_name_is_rejected() -> None:
    with pytest.raises(ValidationError, match="invalid identifier"):
        PipelineRequirement.model_validate({**make_valid_requirement().model_dump(), "pipeline_name": "daily revenue"})


def test_invalid_table_identifier_is_rejected() -> None:
    with pytest.raises(ValidationError, match="invalid identifier"):
        TableReference(schema_name="raw", table_name="orders;drop")


def test_missing_source_is_rejected() -> None:
    payload = make_valid_requirement().model_dump()
    payload["sources"] = []

    with pytest.raises(ValidationError):
        PipelineRequirement.model_validate(payload)


def test_duplicate_sources_are_rejected() -> None:
    payload = make_valid_requirement().model_dump()
    payload["sources"] = [payload["sources"][0], payload["sources"][0]]

    with pytest.raises(ValidationError, match="duplicate source table"):
        PipelineRequirement.model_validate(payload)


def test_valid_incremental_load() -> None:
    strategy = LoadStrategy(load_type="incremental", incremental_column="updated_at", deduplication_keys=["order_id"])

    assert strategy.incremental_column == "updated_at"
    assert strategy.deduplication_keys == ["order_id"]


def test_invalid_incremental_configuration_is_rejected() -> None:
    with pytest.raises(ValidationError, match="incremental load requires"):
        LoadStrategy(load_type="incremental")


def test_full_load_rejects_incremental_columns() -> None:
    with pytest.raises(ValidationError, match="full load cannot define"):
        LoadStrategy(load_type="full", watermark_column="updated_at")


def test_invalid_target_write_mode_is_rejected() -> None:
    with pytest.raises(ValidationError):
        PipelineTarget(table=TableReference(schema_name="curated", table_name="customer_revenue"), write_mode="upsert")


def test_transformation_rule_validation() -> None:
    with pytest.raises(ValidationError, match="join transformation requires"):
        TransformationRule(
            rule_type="join",
            description="Invalid join.",
            input_columns=["customer_id"],
            expression={"left_column": "customer_id"},
        )

    with pytest.raises(ValidationError, match="unsafe SQL"):
        TransformationRule(
            rule_type="filter",
            description="Unsafe filter.",
            input_columns=["order_status"],
            expression={"condition": "select * from raw.orders"},
        )


def test_schedule_validation() -> None:
    with pytest.raises(ValidationError):
        ScheduleDefinition(frequency="monthly", timezone="UTC", enabled=True)

    with pytest.raises(ValidationError, match="manual schedules"):
        ScheduleDefinition(frequency="manual", timezone="UTC", enabled=True)


def test_serialization_to_dict_and_json() -> None:
    requirement = make_valid_requirement()

    dumped = requirement.model_dump()
    encoded = requirement.model_dump_json()

    assert dumped["target"]["write_mode"] == "merge"
    assert json.loads(encoded)["pipeline_name"] == "customer_revenue_daily"


def test_example_requirement_is_valid_and_deterministic() -> None:
    requirement = customer_revenue_daily_requirement()

    assert requirement.pipeline_name == "customer_revenue_daily"
    assert [source.table.qualified_name for source in requirement.sources] == ["raw.customers", "raw.orders"]
    assert requirement.target.table.qualified_name == "curated.customer_revenue"
    assert requirement.load_strategy.load_type == "incremental"
    assert requirement.schedule.frequency == "daily"
