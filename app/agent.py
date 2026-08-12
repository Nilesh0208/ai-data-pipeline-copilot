"""AI agent API routes."""

from __future__ import annotations

from fastapi import APIRouter

from agent.pipeline_agent import PipelineAgentRequest, PipelineAgentResult, generate_pipeline_requirement_result


router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/requirements", response_model=PipelineAgentResult)
def create_requirement(request: PipelineAgentRequest) -> PipelineAgentResult:
    """Generate a validated pipeline requirement from natural language."""
    return generate_pipeline_requirement_result(request.request)