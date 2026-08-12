"""Read-only metadata API routes."""

from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, HTTPException

from agent.tools.metadata_tools import (
    InvalidIdentifierError,
    InvalidLimitError,
    MetadataDatabaseError,
    get_column_metadata,
    get_pipeline_metadata,
    get_row_count,
    get_sample_records,
    get_table_metadata,
    inspect_schema,
    list_tables,
)


router = APIRouter(prefix="/metadata", tags=["metadata"])


@router.get("/tables")
def read_tables() -> list[dict[str, str]]:
    """Return available business tables."""
    return [table.model_dump() for table in _call_tool(list_tables)]


@router.get("/schema/{schema_name}/{table_name}")
def read_schema(schema_name: str, table_name: str) -> dict[str, object]:
    """Return physical schema details for a table."""
    result = _call_tool(inspect_schema, schema_name, table_name)
    _raise_if_not_found(result)
    return result.model_dump()


@router.get("/table/{schema_name}/{table_name}")
def read_table_metadata(schema_name: str, table_name: str) -> dict[str, object]:
    """Return table-level business metadata."""
    result = _call_tool(get_table_metadata, schema_name, table_name)
    _raise_if_not_found(result)
    return result.model_dump()


@router.get("/columns/{schema_name}/{table_name}")
def read_column_metadata(schema_name: str, table_name: str) -> dict[str, object]:
    """Return column-level business metadata."""
    result = _call_tool(get_column_metadata, schema_name, table_name)
    _raise_if_not_found(result)
    return result.model_dump()


@router.get("/sample/{schema_name}/{table_name}")
def read_sample_records(schema_name: str, table_name: str, limit: int = 5) -> dict[str, object]:
    """Return bounded sample records for a table."""
    result = _call_tool(get_sample_records, schema_name, table_name, limit)
    _raise_if_not_found(result)
    return result.model_dump()


@router.get("/count/{schema_name}/{table_name}")
def read_row_count(schema_name: str, table_name: str) -> dict[str, object]:
    """Return a table row count."""
    result = _call_tool(get_row_count, schema_name, table_name)
    _raise_if_not_found(result)
    return result.model_dump()


@router.get("/pipeline/{pipeline_name}")
def read_pipeline_metadata(pipeline_name: str) -> dict[str, object]:
    """Return configured pipeline metadata."""
    result = _call_tool(get_pipeline_metadata, pipeline_name)
    _raise_if_not_found(result)
    return result.model_dump()


def _call_tool(function: Callable[..., object], *args: object) -> object:
    try:
        return function(*args)
    except InvalidIdentifierError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except InvalidLimitError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except MetadataDatabaseError as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc


def _raise_if_not_found(result: object) -> None:
    if getattr(result, "found", True) is False:
        raise HTTPException(status_code=404, detail="Metadata resource not found")
