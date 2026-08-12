# Architecture

This document describes the current Phase 5 application. The implemented system includes FastAPI, read-only Metadata Intelligence Tools, SQLAlchemy, the PostgreSQL sample data platform, a strict Pipeline Requirement Model, and an AI Pipeline Agent backed by the Google Gemini API.

## Current Runtime Flow

```text
User
   |
   v
FastAPI
   |
   |-- AI Pipeline Agent
   |      |
   |      v
   |   Gemini API
   |      ^
   |      |
   |   Function Tool Registry / Dispatcher
   |      |
   |      v
   |   Metadata Intelligence Tools
   |      |
   |      v
   |   SQLAlchemy
   |      |
   |      v
   |   PostgreSQL
   |
   |-- Pipeline Requirement API
   |      |
   |      v
   |   Pipeline Requirement Models
   |
   `-- Metadata API
          |
          v
       Metadata Intelligence Tools
```

Final agent output:

```text
PipelineRequirement
```

The agent does not generate SQL, execute SQL, write data, execute pipelines, or generate data-quality rules.

## Components

### User/API

Users and clients interact with the application through HTTP endpoints exposed by FastAPI.

### FastAPI

The application exposes:

- `GET /` for basic application information.
- `GET /health` for application and database health.
- `GET /metadata/tables` for business table discovery.
- `GET /metadata/schema/{schema_name}/{table_name}` for physical schema inspection.
- `GET /metadata/table/{schema_name}/{table_name}` for table-level business metadata.
- `GET /metadata/columns/{schema_name}/{table_name}` for column-level business metadata.
- `GET /metadata/sample/{schema_name}/{table_name}?limit=5` for bounded sample records.
- `GET /metadata/count/{schema_name}/{table_name}` for row counts.
- `GET /metadata/pipeline/{pipeline_name}` for pipeline metadata.
- `POST /requirements/validate` for validating and normalizing structured pipeline requirements.
- `GET /requirements/example` for the deterministic `customer_revenue_daily` requirement example.
- `POST /agent/requirements` for natural-language requirement generation.

### AI Pipeline Agent

The agent layer lives in `agent/`:

- `client.py` creates a Gemini client from settings only when AI functionality is invoked.
- `prompts.py` contains maintainable agent instructions.
- `tool_registry.py` defines the exact read-only metadata tools exposed to Gemini and dispatches calls deterministically.
- `pipeline_agent.py` implements the Gemini function-calling loop and validates final structured output.

The agent sends user requirements and function declarations to Gemini. When the model requests metadata, the local dispatcher validates the arguments, executes the registered read-only metadata tool, serializes the result, and returns it as a Gemini function response. The loop supports multiple tool calls and enforces a maximum iteration limit.

The final response is requested as structured JSON using the `PipelineRequirement` schema and is validated with the existing Pydantic model before the API returns it.

### Function Tool Registry / Dispatcher

The registry exposes only these tools:

- `list_tables`
- `inspect_schema`
- `get_table_metadata`
- `get_column_metadata`
- `get_sample_records`
- `get_row_count`
- `get_pipeline_metadata`

It rejects unknown tool names, forbids extra tool arguments through Pydantic validation, returns structured tool errors, and does not expose arbitrary SQL or write operations.

### Pipeline Requirement Models

The requirement models define the strict structured contract for agent output:

- `TableReference`
- `PipelineSource`
- `PipelineTarget`
- `TransformationRule`
- `LoadStrategy`
- `ScheduleDefinition`
- `PipelineRequirement`

They reject unsafe identifiers, duplicate source tables, duplicate aliases, invalid enum values, unsafe SQL-like expression strings, inconsistent load settings, and invalid schedule settings.

### Metadata Intelligence Tools

The metadata tools live in `agent/tools/metadata_tools.py`. They provide read-only functions for listing business tables, inspecting schemas, reading table and column descriptions, returning bounded samples, returning row counts, and reading configured pipeline metadata.

The tools do not accept arbitrary SQL strings and do not implement write operations.

### Configuration

Runtime configuration is loaded from environment variables through `pydantic-settings`. `.env.example` documents PostgreSQL settings and Gemini settings:

- `GEMINI_API_KEY` is required only when invoking AI functionality.
- `GEMINI_MODEL` is optional and defaults to `gemini-3.6-flash`.

API keys are not logged and are not hardcoded.

### Database Layer

The database layer uses SQLAlchemy 2.x with the `psycopg` driver. It provides a reusable engine and a lightweight health check. PostgreSQL connection attempts use a short driver timeout so database health checks fail fast when PostgreSQL is unavailable.

### PostgreSQL

Docker Compose defines PostgreSQL with non-public business schemas:

- `raw.customers`
- `raw.orders`
- `curated.customer_revenue`

Metadata tables:

- `metadata.table_metadata`
- `metadata.column_metadata`
- `metadata.pipeline_metadata`
- `metadata.pipeline_runs`

`metadata.pipeline_metadata` contains one planned pipeline definition named `customer_revenue_daily`.

## Future Modules

Future phases may add:

- SQL Generation
- Data Quality Rule Generation
- Pipeline Planning
- Final guardrails

These are intentionally not implemented in Phase 5.