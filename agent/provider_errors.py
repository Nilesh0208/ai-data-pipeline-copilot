"""Shared safe handling for Gemini provider failures."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProviderErrorContext:
    """Sanitized provider failure details safe for routing and logging."""

    http_status: int
    public_message: str
    request_id: str | None


def classify_gemini_error(exc: BaseException, component: str) -> ProviderErrorContext:
    """Map a Gemini SDK exception to a public HTTP-safe failure category."""
    status_code = getattr(exc, "status_code", None)
    code = str(getattr(exc, "code", "") or "").lower()
    request_id = extract_provider_request_id(exc)

    if status_code == 429 or "quota" in code or "resource_exhausted" in code or "rate" in code:
        return ProviderErrorContext(
            http_status=429,
            public_message=f"{component} failed because Gemini quota or rate limit was exceeded",
            request_id=request_id,
        )
    if isinstance(status_code, int) and status_code >= 500:
        return ProviderErrorContext(
            http_status=503,
            public_message=f"{component} failed because Gemini is temporarily unavailable",
            request_id=request_id,
        )
    return ProviderErrorContext(
        http_status=503,
        public_message=f"{component} failed with Gemini",
        request_id=request_id,
    )


def log_gemini_error(logger: logging.Logger, component: str, exc: BaseException) -> ProviderErrorContext:
    """Log sanitized Gemini failure metadata and return the public classification."""
    context = classify_gemini_error(exc, component)
    logger.warning(
        "%s failed: provider=gemini error_type=%s status_code=%s code=%s request_id=%s",
        component,
        exc.__class__.__name__,
        getattr(exc, "status_code", None),
        getattr(exc, "code", None),
        context.request_id,
    )
    return context


def extract_provider_request_id(exc: BaseException) -> str | None:
    """Extract a request id from known Gemini exception shapes."""
    for attribute in ("request_id", "response_id"):
        value = getattr(exc, attribute, None)
        if value:
            return str(value)
    response = getattr(exc, "response", None)
    headers: Any = getattr(response, "headers", {}) or {}
    return headers.get("x-request-id") or headers.get("x-goog-request-id")
