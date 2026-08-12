# AI Data Pipeline Copilot

AI Data Pipeline Copilot is a portfolio project for an agentic AI system that understands data pipeline requirements, inspects metadata, and produces validated pipeline specifications.

## Current Status

Phase 5 is complete. The project now includes the FastAPI/PostgreSQL foundation, deterministic Metadata Intelligence Tools, a strict Pipeline Requirement Model, and a Google Gemini based AI agent core that converts natural-language requests into validated `PipelineRequirement` objects.

Implemented now:

- FastAPI service foundation.
- Environment-based configuration.
- Reusable SQLAlchemy 2.x database engine with psycopg.
- PostgreSQL Docker Compose setup.
- Database health checks.
- Sample PostgreSQL schemas and deterministic seed data.
- Metadata tables describing sample business tables and one planned pipeline.
- Read-only Python metadata tools for table discovery, schema inspection, metadata lookup, samples, row counts, and pipeline metadata.
- Thin read-only FastAPI metadata endpoints.
- Strict Pydantic models for structured pipeline requirements.
- Requirement validation endpoints.
- Google Gemini API integration.
- Google Gen AI Python SDK integration.
- Gemini function-calling agent loop.
- Gemini structured JSON output validated into `PipelineRequirement`.
- `POST /agent/requirements` natural-language requirement endpoint.

## AI Agent Core

The agent lives in `agent/` and uses the official Google Gen AI Python SDK directly. It does not use LangChain, CrewAI, AutoGen, or another agent framework.

Current workflow:

```text
Natural-language request
   |
   v
Gemini AI Pipeline Agent
   |
   v
Gemini function calling
   |
   v
Metadata Intelligence Tools
   |
   v
PostgreSQL
   |
   v
Validated PipelineRequirement
```

The agent can call only registered read-only metadata tools. Tool calls are dispatched through a deterministic registry that validates arguments, rejects unknown tool names, serializes results safely, and records a lightweight trace of tool name, sanitized arguments, and success/failure. The final model output is requested as Gemini structured JSON using the `PipelineRequirement` schema and is validated again with the existing Pydantic model.

Current AI limitations:

- No SQL generation yet.
- No SQL execution.
- No pipeline execution.
- No data-quality rule generation yet.
- No remediation, orchestration, Airflow, or scheduling execution.

## Metadata Intelligence Tools

The metadata tool layer lives in `agent/tools/metadata_tools.py`. These functions are deterministic, read-only, and available to the Gemini agent through `agent/tool_registry.py`:

- `list_tables()` returns business tables from `raw` and `curated`.
- `inspect_schema(schema_name, table_name)` returns physical column names, data types, nullability, primary-key flags, and ordinal positions.
- `get_table_metadata(schema_name, table_name)` reads table descriptions from `metadata.table_metadata`.
- `get_column_metadata(schema_name, table_name)` reads column descriptions from `metadata.column_metadata`.
- `get_sample_records(schema_name, table_name, limit=5)` returns validated sample records. Direct tool usage supports up to 20; agent usage is capped at 10.
- `get_row_count(schema_name, table_name)` returns a table row count.
- `get_pipeline_metadata(pipeline_name)` reads pipeline definitions from `metadata.pipeline_metadata`.

The tools do not accept arbitrary SQL and do not perform writes.

## Pipeline Requirement Model

The pipeline requirement model lives in `pipeline/requirements.py`. It defines the structured contract produced by the AI agent.

Implemented models include:

- `TableReference` for safe schema and table identifiers.
- `PipelineSource` for source table definitions with optional aliases and descriptions.
- `PipelineTarget` for target table definitions with controlled write modes: `append`, `overwrite`, and `merge`.
- `TransformationRule` for declarative transformation intent using controlled rule types: `filter`, `join`, `aggregate`, `derive`, and `rename`.
- `LoadStrategy` for controlled `full` and `incremental` load configuration.
- `ScheduleDefinition` for configuration-only schedules: `manual`, `hourly`, `daily`, and `weekly`.
- `PipelineRequirement` as the top-level validated pipeline specification.

The model rejects unsafe identifiers, duplicate source tables, invalid enum values, inconsistent incremental load settings, unsafe SQL-like expression strings, and invalid schedule configuration.

## Technology Stack

- Python 3.11+
- FastAPI
- PostgreSQL
- SQLAlchemy 2.x
- Pydantic and pydantic-settings
- psycopg
- Google Gemini API
- Google Gen AI Python SDK
- Gemini function calling
- Gemini structured outputs
- Agent orchestration
- Pydantic validated agent output
- pytest
- Docker Compose
- Python logging

## Project Structure

```text
app/             FastAPI application entry points and routes
config/          Environment-based settings
database/        SQLAlchemy engine, health checks, and SQL initialization files
database/sql/    Ordered SQL scripts for schemas, tables, seed data, and metadata
scripts/         Database initialization and verification scripts
agent/           AI agent core, Gemini client, prompts, registry, and tools
agent/tools/     Read-only metadata intelligence tools
pipeline/        Pipeline requirement models and deterministic examples
quality/         Placeholder for future data-quality modules
tests/           Unit tests
docs/            Architecture documentation
```

## Local Setup

Create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install dependencies:

```powershell
pip install -r requirements.txt
```

Create local configuration from the example when needed:

```powershell
Copy-Item .env.example .env
```

Update `POSTGRES_PASSWORD` for local use. Add `GEMINI_API_KEY` only when you want to run the live AI endpoint. Do not commit real secrets. `GEMINI_MODEL` is optional and defaults to `gemini-3.6-flash`.

## PostgreSQL Startup

Start PostgreSQL:

```powershell
docker compose up -d postgres
```

PostgreSQL is exposed on host port `5434`.

Initialize the sample data platform:

```powershell
python scripts/init_database.py
```

Verify expected schemas, tables, and seed counts:

```powershell
python scripts/verify_database.py
```

## FastAPI Startup

Run the API locally:

```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Useful endpoints:

- `GET /`
- `GET /health`
- `GET /metadata/tables`
- `GET /metadata/schema/{schema_name}/{table_name}`
- `GET /metadata/table/{schema_name}/{table_name}`
- `GET /metadata/columns/{schema_name}/{table_name}`
- `GET /metadata/sample/{schema_name}/{table_name}?limit=5`
- `GET /metadata/count/{schema_name}/{table_name}`
- `GET /metadata/pipeline/{pipeline_name}`
- `POST /requirements/validate`
- `GET /requirements/example`
- `POST /agent/requirements`

Example agent request after setting `GEMINI_API_KEY`:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/agent/requirements -ContentType 'application/json' -Body '{"request":"Create a daily pipeline that joins customers and completed orders and calculates total revenue per customer."}'
```

If `GEMINI_API_KEY` is missing, the agent endpoint returns a controlled error response instead of crashing.

## Tests

Run the test suite:

```powershell
pytest
```

Run Python syntax validation:

```powershell
python -m compileall app config database agent pipeline quality scripts tests
```

The unit tests mock the Gemini client and do not make billable Gemini API calls.

## Current Limitations

- No generated SQL.
- No SQL execution.
- No data writes.
- No generated data-quality rules.
- No pipeline execution or scheduling execution.
- No Airflow or orchestration engine.
- No CI/CD.
- No frontend.