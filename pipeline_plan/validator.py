"""Deterministic validation for generated pipeline plans."""

from __future__ import annotations

import re
from collections import defaultdict, deque

from pipeline.requirements import PipelineRequirement
from pipeline_plan.models import PipelinePlan, PipelinePlanStep, PipelinePlanStepType, PipelinePlanValidationStatus
from quality.models import GeneratedDataQualityPlan, QualityValidationStatus
from sql_generation.models import GeneratedSQL, SQLValidationStatus


QUALIFIED_TABLE_PATTERN = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")


def validate_pipeline_plan(
    requirement: PipelineRequirement,
    generated_sql: GeneratedSQL,
    quality_plan: GeneratedDataQualityPlan,
    pipeline_plan: PipelinePlan,
) -> PipelinePlan:
    """Return a pipeline plan annotated with deterministic validation results."""
    errors: list[str] = []
    warnings = list(pipeline_plan.warnings)

    expected_sources = [source.table.qualified_name for source in requirement.sources]
    expected_target = requirement.target.table.qualified_name
    allowed_tables = set(expected_sources + [expected_target])

    _validate_artifact_consistency(requirement, generated_sql, quality_plan, pipeline_plan, expected_sources, expected_target, errors)
    _validate_schedule(requirement, pipeline_plan, errors)
    _validate_steps(pipeline_plan.execution_steps, errors)
    _validate_step_references(pipeline_plan.execution_steps, allowed_tables, errors)
    _validate_logical_ordering(pipeline_plan.execution_steps, expected_target, errors)
    _validate_quality_references(quality_plan, pipeline_plan, errors, warnings)

    status = _status_for(errors, warnings)
    return pipeline_plan.model_copy(
        update={
            "validation_status": status,
            "validation_errors": _deduplicate(errors),
            "warnings": _deduplicate(warnings),
        }
    )


def _validate_artifact_consistency(
    requirement: PipelineRequirement,
    generated_sql: GeneratedSQL,
    quality_plan: GeneratedDataQualityPlan,
    pipeline_plan: PipelinePlan,
    expected_sources: list[str],
    expected_target: str,
    errors: list[str],
) -> None:
    if pipeline_plan.pipeline_name != requirement.pipeline_name:
        errors.append("pipeline_name does not match PipelineRequirement")
    if generated_sql.pipeline_name != requirement.pipeline_name:
        errors.append("GeneratedSQL pipeline_name does not match PipelineRequirement")
    if quality_plan.pipeline_name != requirement.pipeline_name:
        errors.append("GeneratedDataQualityPlan pipeline_name does not match PipelineRequirement")
    if generated_sql.source_tables != expected_sources:
        errors.append("GeneratedSQL source_tables do not match PipelineRequirement sources")
    if generated_sql.target_table != expected_target:
        errors.append("GeneratedSQL target_table does not match PipelineRequirement target")
    if generated_sql.validation_status != SQLValidationStatus.VALID:
        errors.append("GeneratedSQL must be locally valid before pipeline planning")
    if quality_plan.validation_status == QualityValidationStatus.INVALID:
        errors.append("GeneratedDataQualityPlan must be locally valid before pipeline planning")


def _validate_schedule(requirement: PipelineRequirement, pipeline_plan: PipelinePlan, errors: list[str]) -> None:
    if pipeline_plan.schedule.frequency != requirement.schedule.frequency:
        errors.append("PipelinePlan schedule frequency does not match PipelineRequirement")
    if pipeline_plan.schedule.timezone != requirement.schedule.timezone:
        errors.append("PipelinePlan schedule timezone does not match PipelineRequirement")
    if pipeline_plan.schedule.enabled != requirement.schedule.enabled:
        errors.append("PipelinePlan schedule enabled flag does not match PipelineRequirement")


def _validate_steps(steps: list[PipelinePlanStep], errors: list[str]) -> None:
    step_ids = [step.step_id for step in steps]
    if len(step_ids) != len(set(step_ids)):
        errors.append("execution_steps must contain unique step_id values")
        return

    step_id_set = set(step_ids)
    for step in steps:
        if step.step_id in step.depends_on:
            errors.append(f"step {step.step_id} cannot depend on itself")
        for dependency in step.depends_on:
            if dependency not in step_id_set:
                errors.append(f"step {step.step_id} depends on unknown step: {dependency}")

    cycle_path = _find_cycle(steps)
    if cycle_path:
        errors.append(f"execution_steps contain a circular dependency: {' -> '.join(cycle_path)}")


def _validate_step_references(steps: list[PipelinePlanStep], allowed_tables: set[str], errors: list[str]) -> None:
    for step in steps:
        for reference in step.inputs + step.outputs:
            if QUALIFIED_TABLE_PATTERN.fullmatch(reference) and reference not in allowed_tables:
                errors.append(f"step {step.step_id} references unrelated table: {reference}")


def _validate_logical_ordering(steps: list[PipelinePlanStep], expected_target: str, errors: list[str]) -> None:
    step_by_type: dict[PipelinePlanStepType, list[PipelinePlanStep]] = defaultdict(list)
    for step in steps:
        step_by_type[PipelinePlanStepType(step.step_type)].append(step)

    transformations = step_by_type.get(PipelinePlanStepType.TRANSFORMATION, [])
    loads = step_by_type.get(PipelinePlanStepType.LOAD, [])
    for load_step in loads:
        if transformations and not any(_is_ancestor(transform.step_id, load_step.step_id, steps) for transform in transformations):
            errors.append(f"load step {load_step.step_id} must depend on transformation work")

    for quality_step in step_by_type.get(PipelinePlanStepType.QUALITY_VALIDATION, []):
        references_target = expected_target in quality_step.inputs or expected_target in quality_step.outputs
        if references_target and loads and not any(_is_ancestor(load.step_id, quality_step.step_id, steps) for load in loads):
            errors.append(f"quality_validation step {quality_step.step_id} for target must depend on load")


def _validate_quality_references(
    quality_plan: GeneratedDataQualityPlan,
    pipeline_plan: PipelinePlan,
    errors: list[str],
    warnings: list[str],
) -> None:
    known_rules = {rule.rule_name for rule in quality_plan.rules}
    unknown_rules = sorted(set(pipeline_plan.quality_checks) - known_rules)
    if unknown_rules:
        errors.append(f"PipelinePlan references unknown quality checks: {', '.join(unknown_rules)}")
    if quality_plan.rules and not pipeline_plan.quality_checks:
        warnings.append("quality_plan contains rules but PipelinePlan quality_checks is empty")


def _find_cycle(steps: list[PipelinePlanStep]) -> list[str]:
    graph = {step.step_id: list(step.depends_on) for step in steps}
    visiting: set[str] = set()
    visited: set[str] = set()
    path: list[str] = []

    def visit(node: str) -> bool:
        if node in visiting:
            path.append(node)
            return True
        if node in visited:
            return False
        visiting.add(node)
        path.append(node)
        for dependency in graph.get(node, []):
            if dependency in graph and visit(dependency):
                return True
        visiting.remove(node)
        visited.add(node)
        path.pop()
        return False

    for node in graph:
        if visit(node):
            cycle_start = path[-1]
            start_index = path.index(cycle_start)
            return path[start_index:] + [cycle_start]
    return []


def _is_ancestor(ancestor_step_id: str, step_id: str, steps: list[PipelinePlanStep]) -> bool:
    step_map = {step.step_id: step for step in steps}
    queue = deque(step_map[step_id].depends_on if step_id in step_map else [])
    seen: set[str] = set()
    while queue:
        candidate = queue.popleft()
        if candidate == ancestor_step_id:
            return True
        if candidate in seen or candidate not in step_map:
            continue
        seen.add(candidate)
        queue.extend(step_map[candidate].depends_on)
    return False


def _status_for(errors: list[str], warnings: list[str]) -> PipelinePlanValidationStatus:
    if errors:
        return PipelinePlanValidationStatus.INVALID
    if warnings:
        return PipelinePlanValidationStatus.VALID_WITH_WARNINGS
    return PipelinePlanValidationStatus.VALID


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
