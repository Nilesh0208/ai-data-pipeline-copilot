"""Initialize the Phase 2 sample data platform."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from sqlalchemy.engine import Engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.connection import get_engine


logger = logging.getLogger(__name__)
SQL_DIRECTORY = PROJECT_ROOT / "database" / "sql"


def discover_sql_files(sql_directory: Path = SQL_DIRECTORY) -> list[Path]:
    """Return initialization SQL files in deterministic execution order."""
    if not sql_directory.exists():
        raise FileNotFoundError(f"SQL directory does not exist: {sql_directory}")

    sql_files = sorted(sql_directory.glob("*.sql"))
    if not sql_files:
        raise FileNotFoundError(f"No SQL files found in: {sql_directory}")

    return sql_files


def execute_sql_file(engine: Engine, sql_file: Path) -> None:
    """Execute one SQL file inside a transaction."""
    sql = sql_file.read_text(encoding="utf-8").strip()
    if not sql:
        logger.info("Skipping empty SQL file: %s", sql_file.name)
        return

    logger.info("Executing SQL initialization file: %s", sql_file.name)
    with engine.begin() as connection:
        connection.exec_driver_sql(sql)


def initialize_database(engine: Engine | None = None, sql_directory: Path = SQL_DIRECTORY) -> list[Path]:
    """Execute all database initialization SQL files in order."""
    database_engine = engine or get_engine()
    sql_files = discover_sql_files(sql_directory)

    for sql_file in sql_files:
        execute_sql_file(database_engine, sql_file)

    logger.info("Database initialization completed successfully with %d SQL files.", len(sql_files))
    return sql_files


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    initialize_database()


if __name__ == "__main__":
    main()