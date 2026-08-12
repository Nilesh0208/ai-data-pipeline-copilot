"""SQL generation API routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from agent.client import MissingGeminiAPIKeyError
from pipeline.requirements import PipelineRequirement
from sql_generation.generator import SQLGenerationError, SQLStructuredOutputError, generate_sql
from sql_generation.models import GeneratedSQL


router = APIRouter(prefix="/sql", tags=["sql-generation"])


@router.post("/generate", response_model=GeneratedSQL)
def generate_pipeline_sql(requirement: PipelineRequirement) -> GeneratedSQL:
    """Generate inspectable PostgreSQL SQL for a validated pipeline requirement."""
    try:
        return generate_sql(requirement)
    except MissingGeminiAPIKeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SQLStructuredOutputError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except SQLGenerationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc