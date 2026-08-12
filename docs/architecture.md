# Architecture

This document describes the current Phase 3 application. The implemented system includes FastAPI, read-only Metadata Intelligence Tools, SQLAlchemy, and the PostgreSQL sample data platform. AI agents, prompt flows, OpenAI API integration, SQL generation, pipeline execution, scheduling, and data-quality rule generation are future work and are not implemented yet.

## Current Runtime Flow

```text
User
   |
   v
FastAPI
   |
   v
Metadata API
   |
   v
Metadata Intelligence Tools
   |
   v
SQLAlchemy
   |
   v
PostgreSQL
   |-- raw
   |   |-- customers
   |   `-- orders
   |
   |-- curated
   |   `-- customer_revenue
   |
   `-- metadata
       |-- table_metadata
       |-- column_metadata
       |-- pipeline_metadata
       `-- pipeline_runs
```

## Future Agent Flow

```text
Future AI Agent
   |
   v
Metadata Intelligence Tools
```

The future AI agent is NOT YET IMPLEMENTED. The Phase 3 metadata tools are deterministic Python functions that work without an LLM.

## Components

### User/API

Users and clients interact with the application through HTTP endpoints exposed by FastAPI.

### FastAPI

The application currently exposes:

- `GET /` for basic application information.
- `GET /health` for application and database health.
- `GET /metadata/tables` for business table discovery.
- `GET /metadata/schema/{schema_name}/{table_name}` for physical schema inspection.
- `GET /metadata/table/{schema_name}/{table_name}` for table-level business metadata.
- `GET /metadata/columns/{schema_name}/{table_name}` for column-level business metadata.
- `GET /metadata/sample/{schema_name}/{table_name}?limit=5` for bounded sample records.
- `GET /metadata/count/{schema_name}/{table_name}` for row counts.
- `GET /metadata/pipeline/{pipeline_name}` for pipeline metadata.

The API handles database connection failures gracefully and returns safe error responses instead of exposing raw database exceptions.

### Metadata API

The metadata API is a thin HTTP layer in `app/metadata.py`. It delegates business and database behavior to deterministic metadata tools and maps tool errors to HTTP responses:

- Invalid identifiers and invalid sample limits return `400`.
- Unknown schemas, tables, or metadata resources return `404`.
- Database failures return `503`.

### Metadata Intelligence Tools

The metadata tools live in `agent/tools/metadata_tools.py`. They provide read-only functions for:

- Listing `raw` and `curated` business tables.
- Inspecting physical table schemas through SQLAlchemy inspection.
- Reading table and column descriptions from the `metadata` schema.
- Returning bounded sample records with safe reflected identifiers.
- Returning table row counts.
- Reading pipeline definitions without executing pipelines.

The tools do not accept arbitrary SQL strings and do not implement write operations.

### Configuration

Runtime configuration is loaded from environment variables through `pydantic-settings`. Defaults are suitable for local development, and `.env.example` documents the expected variables.

Secrets are not committed. The repository includes only an example password placeholder.

### Database Layer

The database layer uses SQLAlchemy 2.x with the `psycopg` driver. It provides a reusable engine and a lightweight `SELECT 1` health check. PostgreSQL connection attempts use a short driver timeout so database health checks fail fast when PostgreSQL is unavailable.

The same database connection configuration is used by initialization scripts, verification scripts, and metadata tools.

### PostgreSQL

Docker Compose defines a single PostgreSQL service with:

- Host port `5434`.
- A persistent named volume.
- A healthcheck.

The sample data platform creates three non-public schemas:

- `raw` for source-like sample tables.
- `curated` for future target tables.
- `metadata` for table, column, and planned pipeline descriptions.

#### Raw Schema

`raw.customers` contains deterministic sample customer records.

`raw.orders` contains deterministic sample order records with a foreign key to `raw.customers`.

#### Curated Schema

`curated.customer_revenue` is a planned target table for future customer revenue aggregation. It exists in Phase 3 but is not populated by pipeline execution.

#### Metadata Schema

`metadata.table_metadata` describes the sample business tables.

`metadata.column_metadata` describes important columns on those tables.

`metadata.pipeline_metadata` contains one planned pipeline definition named `customer_revenue_daily` with sources `raw.customers` and `raw.orders`, target `curated.customer_revenue`, incremental load type, and daily schedule.

`metadata.pipeline_runs` exists for future execution history and remains empty in Phase 3.

## Future Agent Layer

The future agent layer is not implemented in Phase 3. Later phases may add AI orchestration that calls the deterministic metadata tools, but the current tool layer intentionally contains no prompts, model calls, natural-language parsing, SQL generation, data-quality generation, or pipeline execution.