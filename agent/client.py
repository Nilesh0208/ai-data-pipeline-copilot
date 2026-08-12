"""Gemini client factory for agent functionality."""

from __future__ import annotations

from typing import Protocol

from config.settings import Settings, get_settings


class MissingGeminiAPIKeyError(RuntimeError):
    """Raised when AI functionality is invoked without a Gemini API key."""


class GeminiClientProtocol(Protocol):
    """Subset of the Gemini client used by the pipeline agent."""

    models: object


def create_gemini_client(settings: Settings | None = None) -> GeminiClientProtocol:
    """Create a Gemini client from settings without doing network I/O."""
    active_settings = settings or get_settings()
    api_key = active_settings.gemini_api_key.get_secret_value() if active_settings.gemini_api_key else ""
    if not api_key:
        raise MissingGeminiAPIKeyError("GEMINI_API_KEY is required for AI agent functionality")

    from google import genai

    return genai.Client(api_key=api_key)