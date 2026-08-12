# AI Data Pipeline Copilot

AI Data Pipeline Copilot is a portfolio project that will eventually become an agentic AI system for understanding data pipeline requirements, inspecting metadata, generating pipeline plans, SQL transformations, and data-quality rules.

## Current Status

Phase 3 is complete. The project now includes the Phase 1 FastAPI foundation, the Phase 2 PostgreSQL sample data platform, and deterministic Metadata Intelligence Tools that inspect existing database metadata without using an LLM.

Implemented now:

- FastAPI service foundation.
- Environment-based configuration.
- Reusable SQLAlchemy 2.x database engine with psycopg.
- PostgreSQL Docker Compose setup.
- Database health checks.
- Sample PostgreSQL schemas and tables.
- Deterministic seed data for raw source tables.
- Metadata tables describing the sample platform and one planned pipeline definition.
- Read-only Python metadata tools for table discovery, schema inspection, metadata lookup, samples, row counts, and pipeline metadata.
- Thin read-only FastAPI metadata endpoints.

No AI-agent functionality is implemented yet.

## Metadata Intelligence Tools

The metadata tool layer lives in `agent/tools/metadata_tools.py`. These functions are deterministic, read-only, and designed to be called later by a future AI agent:

- `list_tables()` returns business tables from `raw` and `curated`.
- `inspect_schema(schema_name, table_name)` returns physical column names, data types, nullability, primary-key flags, and ordinal positions.
- `get_table_metadata(schema_name, table_name)` reads table descriptions from `metadata.table_metadata`.
- `get_column_metadata(schema_name, table_name)` reads column descriptions from `metadata.column_metadata`.
- `get_sample_records(schema_name, table_name, limit=5)` returns validated sample records with a maximum limit of 20.
- `get_row_count(schema_name, table_name)` returns a table row count.
- `get_pipeline_metadata(pipeline_name)` reads pipeline definitions from `metadata.pipeline_metadata`.

The tools do not accept arbitrary SQL and do not perform writes.

## Planned Future Capabilities

- AI agent orchestration.
- OpenAI API integration.
- Requirement understanding for data pipeline requests.
- Pipeline planning.
- SQL transformation generation.
- Data-quality rule generation.
- Pipeline execution support.

## Technology Stack

- Python 3.11+
- FastAPI
- PostgreSQL
- SQLAlchemy 2.x
- Pydantic and pydantic-settings
- psycopg
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
agent/           Deterministic metadata tools and future agent modules
agent/tools/     Read-only metadata intelligence tools
pipeline/        Placeholder for future pipeline modules
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

Update `POSTGRES_PASSWORD` in `.env` for local use. Do not commit real secrets.

## PostgreSQL Startup

Start PostgreSQL:

```powershell
docker compose up -d postgres
```

PostgreSQL is exposed on host port `5434`.

Check service status:

```powershell
docker compose ps
```

## Database Initialization

Initialize the sample data platform:

```powershell
python scripts/init_database.py
```

The initialization command uses the existing application settings and SQLAlchemy engine. It executes SQL files from `database/sql/` in filename order and is safe to rerun.

Verify expected schemas, tables, and seed counts:

```powershell
python scripts/verify_database.py
```

## Sample Data Platform

Business tables are not created in the `public` schema.

Raw source tables:

- `raw.customers`
- `raw.orders`

Curated target table:

- `curated.customer_revenue`

Metadata tables:

- `metadata.table_metadata`
- `metadata.column_metadata`
- `metadata.pipeline_metadata`
- `metadata.pipeline_runs`

Seed data includes 10 customers and 35 orders with multiple countries, currencies, and order statuses. `curated.customer_revenue` is intentionally not populated by a pipeline in Phase 3.

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

If PostgreSQL is unavailable, `/health` returns a degraded status instead of crashing. Metadata endpoints return safe HTTP errors for unavailable databases, invalid identifiers, invalid sample limits, and unknown resources.

## Tests

Run the test suite:

```powershell
pytest
```

Run Python syntax validation:

```powershell
python -m compileall app config database agent pipeline quality scripts tests
```

## Current Limitations

- No OpenAI API integration.
- No AI agents, prompts, or tool calling.
- No natural-language parsing.
- No generated SQL.
- No generated data-quality rules.
- No pipeline execution or scheduling.
- No CI/CD.
- No frontend.