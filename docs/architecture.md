# Architecture

This document describes the current Phase 1 foundation only. No AI agents, prompt flows, SQL generation, metadata inspection, pipeline execution, or data-quality rule generation are implemented yet.

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

The database layer uses SQLAlchemy 2.x with the `psycopg` driver. It provides a reusable engine and a lightweight `SELECT 1` health check.

No business tables, metadata tables, migrations, or pipeline schemas exist in Phase 1.

### PostgreSQL

Docker Compose defines a single PostgreSQL service with:

- Host port `5434`.
- A persistent named volume.
- A healthcheck.

## Planned Future Work

The `agent`, `pipeline`, and `quality` packages are placeholders for later phases. Planned future capabilities include requirement understanding, metadata inspection, pipeline planning, SQL transformation generation, and data-quality rule generation.
