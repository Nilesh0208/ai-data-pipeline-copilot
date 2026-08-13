"""Deterministic pipeline requirement examples."""

from __future__ import annotations

from pipeline.requirements import (
    LoadStrategy,
    PipelineRequirement,
    PipelineSource,
    PipelineTarget,
    ScheduleDefinition,
    TableReference,
    TransformationRule,
)


def customer_revenue_daily_requirement() -> PipelineRequirement:
    """Return the deterministic customer revenue requirement example."""
    return PipelineRequirement(
        pipeline_name="customer_revenue_daily",
        description="Daily customer revenue aggregation from raw customers and orders.",
        sources=[
            PipelineSource(
                table=TableReference(schema_name="raw", table_name="customers"),
                alias="c",
                description="Raw customer records.",
            ),
            PipelineSource(
                table=TableReference(schema_name="raw", table_name="orders"),
                alias="o",
                description="Raw order records.",
            ),
        ],
        target=PipelineTarget(
            table=TableReference(schema_name="curated", table_name="customer_revenue"),
            write_mode="merge",
        ),
        transformations=[
            TransformationRule(
                rule_type="join",
                description="Join customers and orders by customer_id.",
                input_columns=["c.customer_id", "o.customer_id"],
                expression={"left_column": "c.customer_id", "right_column": "o.customer_id", "join_type": "inner"},
            ),
            TransformationRule(
                rule_type="filter",
                description="Keep completed orders.",
                input_columns=["o.status"],
                expression={"column": "o.status", "operator": "equals", "value": "completed"},
            ),
            TransformationRule(
                rule_type="aggregate",
                description="Calculate order count and total revenue by customer.",
                input_columns=["o.order_id", "o.order_amount"],
                output_column="total_revenue",
                expression={
                    "group_by": ["c.customer_id"],
                    "metrics": [
                        {"name": "total_orders", "function": "count", "column": "o.order_id"},
                        {"name": "total_revenue", "function": "sum", "column": "o.order_amount"},
                    ],
                },
            ),
            TransformationRule(
                rule_type="derive",
                description="Derive the most recent completed order date.",
                input_columns=["o.order_date"],
                output_column="last_order_date",
                expression={"function": "max", "column": "o.order_date"},
            ),
        ],
        load_strategy=LoadStrategy(
            load_type="incremental",
            watermark_column="last_order_date",
            deduplication_keys=["customer_id"],
        ),
        schedule=ScheduleDefinition(frequency="daily", timezone="UTC", enabled=True),
        business_purpose="Provide daily customer revenue metrics for analytics.",
        owner="data_engineering",
        tags=["customer_revenue", "daily"],
    )
