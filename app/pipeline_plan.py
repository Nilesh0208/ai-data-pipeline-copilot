"""Pipeline-plan generation API routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from agent.client import MissingGeminiAPIKeyError
from pipeline_plan.generator import (
    PipelinePlanGenerationError,
    PipelinePlanStructuredOutputError,
    generate_pipeline_plan,
)
from pipeline_plan.models import PipelinePlan, PipelinePlanGenerationRequest


router = APIRouter(prefix="/pipeline-plan", tags=["pipeline-plan-generation"])


@router.post("/generate", response_model=PipelinePlan)
def generate_pipeline_plan_endpoint(request: PipelinePlanGenerationRequest) -> PipelinePlan:
    """Generate an inspectable implementation blueprint for validated pipeline artifacts."""
    try:
        return generate_pipeline_plan(request.requirement, request.generated_sql, request.quality_plan)
    except MissingGeminiAPIKeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PipelinePlanStructuredOutputError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except PipelinePlanGenerationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
