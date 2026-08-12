"""Pipeline requirement validation API routes."""

from __future__ import annotations

from fastapi import APIRouter

from pipeline.examples import customer_revenue_daily_requirement
from pipeline.requirements import PipelineRequirement


router = APIRouter(prefix="/requirements", tags=["requirements"])


@router.post("/validate")
def validate_requirement(requirement: PipelineRequirement) -> dict[str, object]:
    """Return the validated and normalized pipeline requirement."""
    return requirement.model_dump()


@router.get("/example")
def read_example_requirement() -> dict[str, object]:
    """Return the deterministic customer revenue pipeline requirement example."""
    return customer_revenue_daily_requirement().model_dump()
