# AI Data Pipeline Copilot

AI Data Pipeline Copilot is a portfolio project that will eventually become an agentic AI system for understanding data pipeline requirements, inspecting metadata, generating pipeline plans, SQL transformations, and data-quality rules.

## Current Status

Phase 2 is complete as a sample data platform. The project now includes the Phase 1 FastAPI foundation plus PostgreSQL initialization scripts for a small realistic data engineering environment.

Implemented now:

- FastAPI service foundation.
- Environment-based configuration.
- Reusable SQLAlchemy 2.x database engine with psycopg.
- PostgreSQL Docker Compose setup.
- Database health checks.
- Sample PostgreSQL schemas and tables.
- Deterministic seed data for raw source tables.
- Metadata tables describing the sample platform and one planned pipeline definition.

No AI-agent functionality is implemented yet.

## Planned Future Capabilities

- Requirement understanding for data pipeline requests.
- Metadata inspection.
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
database/sql/    Ordered SQL scripts for Phase 2 schemas, tables, seed data, and metadata
scripts/         Database initialization and verification scripts
agent/           Placeholder for future agent modules
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

Initialize the Phase 2 sample data platform:

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

Seed data includes 10 customers and 35 orders with multiple countries, currencies, and order statuses. `curated.customer_revenue` is intentionally not populated by a pipeline in Phase 2.

## FastAPI Startup

Run the API locally:

```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Useful endpoints:

- `GET /`
- `GET /health`

If PostgreSQL is unavailable, `/health` returns a degraded status instead of crashing.

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
- No agents, prompts, or tool calling.
- No natural-language parsing.
- No generated SQL.
- No generated data-quality rules.
- No pipeline execution or scheduling.
- No CI/CD.
- No frontend.