"""User-facing error text that never leaks internal exception detail.

Provider libraries, SQL drivers and file parsers raise messages that can contain
API endpoints, SQL fragments, file paths or echoed request bodies. Those belong in
the server log (``backend.logger`` already redacts secrets), not in an HTTP
response. Endpoints call :func:`public_message` when they have nothing curated to
say, and :func:`log_and_mask` to do both in one step.
"""

from __future__ import annotations

import logging

try:  # pragma: no cover - import shim mirrors the rest of the backend
    from backend.logger import log_connection_failure
except ModuleNotFoundError:  # pragma: no cover
    from logger import log_connection_failure

logger = logging.getLogger("app.errors")

DEFAULT_MESSAGE = "The request could not be completed. Please retry in a moment."

# Exceptions whose message is authored by this codebase and is therefore safe to
# show. Everything else is replaced by ``fallback``.
_SAFE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    ValueError,
    PermissionError,
    FileNotFoundError,
    TimeoutError,
)


def public_message(exc: BaseException | None, fallback: str = DEFAULT_MESSAGE) -> str:
    """Return ``fallback`` for unknown errors, or a short safe message.

    Even "safe" exception types are trimmed and stripped of anything that looks
    like an absolute path or a URL, because a library can raise ``ValueError`` with
    an endpoint inside it.
    """
    if exc is None or not isinstance(exc, _SAFE_EXCEPTIONS):
        return fallback
    message = str(exc).strip()
    lowered = message.casefold()
    if (
        not message
        or "://" in message
        or "\\" in message
        or "/" in message
        or "api_key=" in lowered
        or "token=" in lowered
        or "password=" in lowered
        or "secret=" in lowered
    ):
        return fallback
    return message[:300]


def log_and_mask(
    exc: BaseException,
    *,
    endpoint: str,
    operation: str,
    fallback: str = DEFAULT_MESSAGE,
    user_id: int | None = None,
) -> str:
    """Log the real error server-side (redacted) and return the public message."""
    try:
        log_connection_failure(
            exc,
            endpoint=endpoint,
            payload={"operation": operation, "user": user_id},
            function=operation,
        )
    except Exception:  # noqa: BLE001 - logging must never break a response
        logger.exception("failed to record a diagnostic for %s", operation)
    return public_message(exc, fallback)
