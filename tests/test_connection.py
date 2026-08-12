"""Tests for SQLAlchemy database connection utilities."""

from __future__ import annotations

from unittest.mock import Mock

from config.settings import Settings
from database import connection


def test_get_engine_configures_postgres_connect_timeout(monkeypatch) -> None:
    settings = Settings()
    created_engine = Mock()
    captured: dict[str, object] = {}

    def mock_create_engine(*args: object, **kwargs: object) -> Mock:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return created_engine

    connection.get_engine.cache_clear()
    monkeypatch.setattr(connection, "get_settings", lambda: settings)
    monkeypatch.setattr(connection, "create_engine", mock_create_engine)

    try:
        engine = connection.get_engine()
    finally:
        connection.get_engine.cache_clear()

    assert engine is created_engine
    assert captured["args"] == (connection.build_database_url(settings),)
    assert captured["kwargs"] == {
        "connect_args": {"connect_timeout": connection.POSTGRES_CONNECT_TIMEOUT_SECONDS},
        "pool_pre_ping": True,
        "future": True,
    }