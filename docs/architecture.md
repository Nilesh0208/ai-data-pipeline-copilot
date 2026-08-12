# Architecture

This document describes the current Phase 2 sample data platform. AI agents, prompt flows, SQL generation, metadata inspection tools, pipeline execution, scheduling, and data-quality rule generation are future work and are not implemented yet.

## Current Runtime Flow

```text
User/API
   |
   v
FastAPI
   |
   v
Configuration
   |
   v
Database Layer
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

## Components

### User/API

Users and clients interact with the application through HTTP endpoints exposed by FastAPI.

### FastAPI

The application currently exposes:

- `GET /` for basic application information.
- `GET /health` for application and database health.

The API handles database connection failures gracefully and returns a degraded health response instead of crashing.

### Configuration

Runtime configuration is loaded from environment variables through `pydantic-settings`. Defaults are suitable for local development, and `.env.example` documents the expected variables.

Secrets are not committed. The repository includes only an example password placeholder.

### Database Layer

The database layer uses SQLAlchemy 2.x with the `psycopg` driver. It provides a reusable engine and a lightweight `SELECT 1` health check. PostgreSQL connection attempts use a short driver timeout so database health checks fail fast when PostgreSQL is unavailable.

The same database connection configuration is used by the Phase 2 initialization and verification scripts.

### PostgreSQL

Docker Compose defines a single PostgreSQL service with:

- Host port `5434`.
- A persistent named volume.
- A healthcheck.

The Phase 2 sample data platform creates three non-public schemas:

- `raw` for source-like sample tables.
- `curated` for future target tables.
- `metadata` for table, column, and planned pipeline descriptions.

#### Raw Schema

`raw.customers` contains deterministic sample customer records.

`raw.orders` contains deterministic sample order records with a foreign key to `raw.customers`.

#### Curated Schema

`curated.customer_revenue` is a planned target table for future customer revenue aggregation. It exists in Phase 2 but is not populated by pipeline execution.

#### Metadata Schema

`metadata.table_metadata` describes the sample business tables.

`metadata.column_metadata` describes important columns on those tables.

`metadata.pipeline_metadata` contains one planned pipeline definition named `customer_revenue_daily` with sources `raw.customers` and `raw.orders`, target `curated.customer_revenue`, incremental load type, and daily schedule.

`metadata.pipeline_runs` exists for future execution history and remains empty in Phase 2.

## Future Agent Layer

The `agent`, `pipeline`, and `quality` packages are placeholders for later phases. The future agent layer is not implemented in Phase 2. Planned future capabilities include requirement understanding, metadata inspection, pipeline planning, SQL transformation generation, and data-quality rule generation.