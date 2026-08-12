# AI Data Pipeline Copilot

AI Data Pipeline Copilot is a portfolio project that will eventually become an agentic AI system for understanding data pipeline requirements, inspecting metadata, generating pipeline plans, SQL transformations, and data-quality rules.

## Current Status

Phase 1 is complete as a project foundation only. It includes a FastAPI service, environment-based configuration, a reusable SQLAlchemy database engine, PostgreSQL Docker Compose setup, health checks, tests, and documentation.

No AI-agent functionality is implemented in this phase.

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
app/          FastAPI application entry points and routes
config/       Environment-based settings
database/     SQLAlchemy engine and database health checks
agent/        Placeholder for future agent modules
pipeline/     Placeholder for future pipeline modules
quality/      Placeholder for future data-quality modules
tests/        Unit tests
docs/         Architecture documentation
scripts/      Utility scripts
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
python -m compileall app config database agent pipeline quality tests
```

## Current Limitations

- No OpenAI API integration.
- No agents, prompts, or tool calling.
- No SQL generation.
- No metadata, source, business, or data-quality tables.
- No pipeline execution.
- No CI/CD.
- No frontend.
