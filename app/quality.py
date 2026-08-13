"""Data-quality generation API routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from agent.client import MissingGeminiAPIKeyError
from pipeline.requirements import PipelineRequirement
from quality.generator import QualityGenerationError, QualityStructuredOutputError, generate_quality_plan
from quality.models import GeneratedDataQualityPlan


router = APIRouter(prefix="/quality", tags=["quality-generation"])


@router.post("/generate", response_model=GeneratedDataQualityPlan)
def generate_pipeline_quality_plan(requirement: PipelineRequirement) -> GeneratedDataQualityPlan:
    """Generate inspectable data-quality rules for a validated pipeline requirement."""
    try:
        return generate_quality_plan(requirement)
    except MissingGeminiAPIKeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except QualityStructuredOutputError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except QualityGenerationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
