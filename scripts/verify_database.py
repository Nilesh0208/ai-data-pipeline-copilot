"""Verify the Phase 2 sample data platform objects and seed counts."""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.connection import get_engine


logger = logging.getLogger(__name__)

EXPECTED_SCHEMAS = ("raw", "curated", "metadata")
EXPECTED_TABLES = (
    ("raw", "customers"),
    ("raw", "orders"),
    ("curated", "customer_revenue"),
    ("metadata", "table_metadata"),
    ("metadata", "column_metadata"),
    ("metadata", "pipeline_metadata"),
    ("metadata", "pipeline_runs"),
)
EXPECTED_COUNTS = {
    "raw.customers": 10,
    "raw.orders": 35,
    "metadata.table_metadata": 3,
    "metadata.column_metadata": 20,
    "metadata.pipeline_metadata": 1,
    "metadata.pipeline_runs": 0,
}


@dataclass(frozen=True)
class VerificationResult:
    """Database verification outcome."""

    name: str
    expected: object
    actual: object

    @property
    def passed(self) -> bool:
        return self.expected == self.actual


def verify_database(engine: Engine | None = None) -> list[VerificationResult]:
    """Return verification results for expected Phase 2 database objects."""
    database_engine = engine or get_engine()
    results: list[VerificationResult] = []

    with database_engine.connect() as connection:
        schemas = set(
            connection.execute(
                text(
                    """
                    SELECT schema_name
                    FROM information_schema.schemata
                    WHERE schema_name = ANY(:schemas)
                    """
                ),
                {"schemas": list(EXPECTED_SCHEMAS)},
            ).scalars()
        )
        for schema_name in EXPECTED_SCHEMAS:
            results.append(VerificationResult(f"schema:{schema_name}", True, schema_name in schemas))

        tables = set(
            connection.execute(
                text(
                    """
                    SELECT table_schema, table_name
                    FROM information_schema.tables
                    WHERE table_schema IN ('raw', 'curated', 'metadata')
                    """
                )
            ).all()
        )
        for table in EXPECTED_TABLES:
            results.append(VerificationResult(f"table:{table[0]}.{table[1]}", True, table in tables))

        for table_name, expected_count in EXPECTED_COUNTS.items():
            actual_count = connection.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar_one()
            results.append(VerificationResult(f"count:{table_name}", expected_count, actual_count))

    return results


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    results = verify_database()
    failed = [result for result in results if not result.passed]

    for result in results:
        logger.info("%s expected=%s actual=%s", result.name, result.expected, result.actual)

    if failed:
        raise SystemExit(f"Database verification failed for {len(failed)} checks.")

    logger.info("Database verification completed successfully with %d checks.", len(results))


if __name__ == "__main__":
    main()