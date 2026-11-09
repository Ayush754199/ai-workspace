"""Structured logging for external service and database connections."""

from __future__ import annotations

import json
import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


LOG_FILE = Path(__file__).resolve().with_name("system.log")
LOGGER_NAME = "nexus.connections"
_SENSITIVE_KEY = re.compile(
    r"password|passwd|secret|token|authorization|api.?key|credential|prompt|input|content|"
    r"email|identifier|user.?id|user.?name|username|full.?name|phone|dob|image|file|text|"
    r"response|message",
    re.IGNORECASE,
)
_SECRET_VALUE = re.compile(r"\b(?:hf|AIza|gsk_|sk-or-v1-|fal_sk_|ltxv_|cfat_)[-_A-Za-z0-9]{12,}\b")
_URL_CREDENTIALS = re.compile(r"(://)[^/@\s]+:[^/@\s]+@")
# Provider error bodies can contain request echoes; cap what reaches the log file.
_MAX_LOGGED_ERROR = 800


class _StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        context = getattr(record, "connection_context", {})
        document: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "function": context.get("function", record.funcName),
            "event": record.getMessage(),
        }
        document.update(context)
        if record.exc_info:
            document["traceback"] = self.formatException(record.exc_info)
        return json.dumps(document, ensure_ascii=True, default=str)


def _safe_text(value: str) -> str:
    value = _SECRET_VALUE.sub("[REDACTED_SECRET]", value)
    value = _URL_CREDENTIALS.sub(r"\1[REDACTED]@", value)
    if len(value) > _MAX_LOGGED_ERROR:
        value = value[:_MAX_LOGGED_ERROR] + f"…[truncated {len(value) - _MAX_LOGGED_ERROR} chars]"
    return value


def _safe_endpoint(endpoint: str) -> str:
    endpoint = _safe_text(endpoint)
    try:
        parsed = urlsplit(endpoint)
    except ValueError:
        return endpoint
    if parsed.scheme and parsed.netloc:
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        # Query strings are the usual place a provider echoes a key or a ref, so
        # they are dropped from every logged endpoint.
        return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    return endpoint


def _safe_payload(value: Any, key: str = "") -> Any:
    if _SENSITIVE_KEY.search(key):
        if isinstance(value, (str, bytes, bytearray)):
            return f"[REDACTED; {len(value)} characters]"
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): _safe_payload(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_payload(item, key) for item in value]
    if isinstance(value, (bytes, bytearray)):
        return f"[binary payload; {len(value)} bytes]"
    if isinstance(value, str):
        return _safe_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return f"<{value.__class__.__name__}>"


def _caller_name() -> str:
    import inspect

    frame = inspect.currentframe()
    try:
        caller = frame.f_back.f_back if frame and frame.f_back else None
        return caller.f_code.co_name if caller else "unknown"
    finally:
        del frame


def _connection_logger() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        logger.propagate = False
        formatter = _StructuredFormatter()
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        file_handler = RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(console)
        logger.addHandler(file_handler)
    return logger


connection_logger = _connection_logger()


def log_connection_event(
    event: str,
    *,
    endpoint: str,
    payload: Any = None,
    level: int = logging.INFO,
) -> str:
    """Log a structured event and return the safe details for user-facing diagnostics."""
    context = {
        "function": _caller_name(),
        "endpoint": _safe_endpoint(endpoint),
        "payload": _safe_payload(payload),
    }
    connection_logger.log(level, event, extra={"connection_context": context})
    return json.dumps({"event": event, **context}, ensure_ascii=True, default=str)


def log_connection_failure(
    error: Exception,
    *,
    endpoint: str,
    payload: Any = None,
    function: str | None = None,
) -> str:
    """Log a failure with its exception traceback and return safe diagnostic details."""
    context = {
        "function": function or _caller_name(),
        "endpoint": _safe_endpoint(endpoint),
        "payload": _safe_payload(payload),
        "error": _safe_text(str(error).strip() or error.__class__.__name__),
    }
    connection_logger.error(
        "External connection failed",
        extra={"connection_context": context},
        exc_info=(type(error), error, error.__traceback__),
    )
    return json.dumps(context, ensure_ascii=True, default=str)
