"""Tests for environment-driven settings."""

from __future__ import annotations

from config.settings import Settings


def test_settings_defaults() -> None:
    settings = Settings()

    assert settings.app_name == "AI Data Pipeline Copilot"
    assert settings.app_env == "local"
    assert settings.app_host == "127.0.0.1"
    assert settings.app_port == 8000
    assert settings.log_level == "INFO"
    assert settings.postgres_db == "ai_pipeline_copilot"
    assert settings.postgres_user == "copilot_user"
    assert settings.postgres_port == 5434


def test_settings_load_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("APP_PORT", "9000")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("POSTGRES_HOST", "db.example.local")

    settings = Settings()

    assert settings.app_env == "test"
    assert settings.app_port == 9000
    assert settings.log_level == "DEBUG"
    assert settings.postgres_host == "db.example.local"
