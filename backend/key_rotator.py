"""Thread-safe round-robin rotation for cloud provider API keys."""

from __future__ import annotations

import itertools
import json
import math
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable, Iterator, Sequence
from typing import TypeVar

from dotenv import load_dotenv

from backend.logger import log_connection_event, log_connection_failure
from config import (
    GEMINI_MODEL,
    GEMINI_MODEL_FALLBACKS,
    PROVIDER_RETRY_BACKOFF_SECONDS,
    PROVIDER_RETRY_COOLDOWN_SECONDS,
)

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
load_dotenv(Path(__file__).resolve().with_name(".env"), override=False)

_lock = threading.Lock()
_rotators: dict[str, tuple[tuple["ProviderCredential", ...], Iterator["ProviderCredential"]]] = {}
_RESULT = TypeVar("_RESULT")


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().casefold()
    return normalized.startswith(("replace-with-", "paste_your_", "your_", "<"))


@dataclass(frozen=True)
class ProviderCredential:
    """A provider key and its safe, non-secret environment identifier."""

    value: str
    key_id: str


class ProviderRateLimitError(RuntimeError):
    """Raised after a provider's transient rate or capacity limit survives retries."""

    def __init__(
        self,
        provider: str,
        key_id: str,
        cooldown_seconds: int,
        status_code: int | None,
        diagnostics: str,
        raw_detail: str = "",
    ) -> None:
        self.provider = provider
        self.key_id = key_id
        self.cooldown_seconds = cooldown_seconds
        self.status_code = status_code
        self.diagnostics = diagnostics
        self.raw_detail = raw_detail
        suffix = f" (HTTP {status_code})" if status_code is not None else ""
        super().__init__(f"{provider} rate limit or capacity limit persisted{suffix}.")


def _parse_key_array(name: str) -> tuple[ProviderCredential, ...]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return ()
    try:
        values = json.loads(raw) if raw.startswith("[") else raw.split(",")
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must be a comma-separated list or JSON array.") from exc
    if not isinstance(values, list):
        raise ValueError(f"{name} must be a JSON array or comma-separated list.")
    return tuple(
        ProviderCredential(value.strip(), f"{name}[{index}]")
        for index, value in enumerate(values, start=1)
        if isinstance(value, str)
        and value.strip()
        and not _is_placeholder(value)
    )


def get_provider_credentials(
    provider: str, *, include_numbered: bool = True
) -> tuple[ProviderCredential, ...]:
    """Read the current keys for a provider, preserving stable IDs but never logging values.

    Set ``include_numbered=False`` to read only the key-array/base variables without
    also scanning the environment for numbered ``<PREFIX>_<n>`` entries.
    """
    if provider == "Gemini":
        canonical_credentials = [
            ProviderCredential(value.strip(), f"GEMINI_KEY_{index}")
            for index in range(1, 17)
            if (value := os.getenv(f"GEMINI_KEY_{index}", "").strip())
            and not _is_placeholder(value)
        ]
        if canonical_credentials:
            credentials = canonical_credentials
            numbered_prefix = None
            fallback_name = ""
        else:
            array_name, numbered_prefix, fallback_name = (
                "GEMINI_API_KEYS",
                "GEMINI_API_KEY",
                "GEMINI_API_KEY",
            )
            credentials = list(_parse_key_array(array_name))
    elif provider == "Hugging Face":
        credentials = list(_parse_key_array("HF_TOKENS"))
        numbered_prefix = "HF_TOKEN"
        fallback_name = "HF_API_TOKEN"
    elif provider == "Groq":
        credentials = list(_parse_key_array("GROQ_API_KEYS"))
        # The historical environment spelling is GROQ_APi_KEY; both are accepted
        # because it is already provisioned in existing deployments.
        numbered_prefix = "GROQ_API_KEY"
        fallback_name = "GROQ_APi_KEY"
    elif provider == "OpenRouter":
        credentials = []
        numbered_prefix = "OPENROUTER_KEY"
        fallback_name = "OPENROUTER_KEY"
    else:
        raise ValueError(f"Unsupported key-rotation provider: {provider}")

    if numbered_prefix and include_numbered:
        pattern = re.compile(rf"^{re.escape(numbered_prefix)}_(\d+)$")
        numbered: list[tuple[int, ProviderCredential]] = []
        for name, raw_value in os.environ.items():
            match = pattern.fullmatch(name)
            normalized_value = raw_value.strip()
            if match and normalized_value and not _is_placeholder(normalized_value):
                numbered.append(
                    (int(match.group(1)), ProviderCredential(normalized_value, name))
                )
        numbered.sort(key=lambda item: item[0])
        credentials.extend(credential for _, credential in numbered)
    if not credentials:
        fallback = os.getenv(fallback_name, "").strip()
        if fallback and not _is_placeholder(fallback):
            credentials.append(ProviderCredential(fallback, fallback_name))

    unique: list[ProviderCredential] = []
    seen: set[str] = set()
    for credential in credentials:
        if credential.value not in seen:
            unique.append(credential)
            seen.add(credential.value)
    return tuple(unique)


def _next_credential(
    provider: str,
    credentials: tuple[ProviderCredential, ...] | None = None,
) -> ProviderCredential:
    credentials = credentials or get_provider_credentials(provider)
    if not credentials:
        raise RuntimeError(
            f"No {provider} credentials configured. Set the provider key array or numbered keys."
        )
    with _lock:
        current = _rotators.get(provider)
        if current is None or current[0] != credentials:
            cycle = itertools.cycle(credentials)
            _rotators[provider] = (credentials, cycle)
        else:
            cycle = current[1]
        next_credential: Callable[[], ProviderCredential] = cycle.__next__
        return next_credential()


def get_next_hf_token() -> str:
    """Return the next configured Hugging Face token."""
    return _next_credential("Hugging Face").value


def get_next_gemini_key() -> str:
    """Return the next configured Gemini key."""
    return _next_credential("Gemini").value


def get_next_groq_key() -> str:
    """Return the next configured Groq key."""
    return _next_credential("Groq").value


def _error_status(error: Exception) -> int | None:
    for candidate in (error, getattr(error, "response", None)):
        if candidate is None:
            continue
        for attribute in ("status_code", "status"):
            value = getattr(candidate, attribute, None)
            if isinstance(value, int):
                return value
        code = getattr(candidate, "code", None)
        if isinstance(code, int):
            return code
        code_value = getattr(code, "value", None)
        if isinstance(code_value, int):
            return code_value
    status_match = re.search(
        r"(?:http(?:\s+status)?|status(?:\s+code|_code)?)\D{0,12}(429|503)\b",
        str(error),
        re.IGNORECASE,
    )
    if status_match:
        return int(status_match.group(1))
    server_error_match = re.search(
        r"\b(429|503)\s+(?:client|server)\s+error\b",
        str(error),
        re.IGNORECASE,
    )
    if server_error_match:
        return int(server_error_match.group(1))
    return None


def is_retired_model_error(error: Exception) -> bool:
    """Recognize a model that the provider has withdrawn (404 / not-found).

    Every key in the pool will fail identically, and retrying other keys only
    multiplies the error, so this must fail fast rather than rotate.
    """
    status = _error_status(error)
    description = f"{error.__class__.__name__} {error}".casefold()
    mentions_model = "model" in description
    return mentions_model and (
        status == 404
        or "no longer available" in description
        or "not_found" in description
        or "is not found" in description
    )


def is_rate_limit_error(error: Exception) -> bool:
    """Recognize provider rate limits, quota exhaustion, and temporary GPU capacity."""
    if is_retired_model_error(error):
        return False
    status = _error_status(error)
    description = f"{error.__class__.__name__} {error}".casefold()
    return status in {429, 503} or any(
        indicator in description
        for indicator in (
            "resource_exhausted",
            "quotaexceeded",
            "quota exceeded",
            "rate limit",
            "too many requests",
            "gpu busy",
            "queue full",
            "queue is full",
            "currently busy",
            "temporarily unavailable",
        )
    )


def _retryable_status(error: Exception) -> int | None:
    status = _error_status(error)
    return status if status in {429, 503} else None


def _suggested_cooldown(error: Exception) -> int:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", {}) or {}
    retry_after = headers.get("Retry-After") if hasattr(headers, "get") else None
    if retry_after is None:
        retry_after = getattr(error, "retry_after", None)
    try:
        value = int(float(retry_after))
    except (TypeError, ValueError, OverflowError):
        retry_match = re.search(
            r"retry in\s+(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?",
            str(error),
            re.IGNORECASE,
        )
        if not retry_match or not any(retry_match.groups()):
            return PROVIDER_RETRY_COOLDOWN_SECONDS
        hours, minutes, seconds = retry_match.groups()
        value = math.ceil(
            (int(hours or 0) * 3600)
            + (int(minutes or 0) * 60)
            + float(seconds or 0)
        )
    return max(1, value)


def _quota_summary(error: Exception) -> str:
    """Reduce a provider quota error to its human-meaningful sentence."""
    message = str(error)
    match = re.search(r"Quota exceeded for metric:[^.]*\.", message)
    if match:
        return match.group(0)[:240]
    return message.strip()[:240]


def _is_daily_quota_exhausted(error: Exception) -> bool:
    message = str(error).casefold()
    return (
        "per day limit exceeded" in message
        or ("requests per day" in message and "quota exceeded" in message)
        or bool(re.search(r"\blimit\s*:\s*0\b", message))
    )


def _ordered_credentials(provider: str) -> tuple[ProviderCredential, ...]:
    credentials = get_provider_credentials(provider)
    if not credentials:
        _next_credential(provider)
    first = _next_credential(provider, credentials)
    remaining = list(credentials)
    start = next(index for index, item in enumerate(remaining) if item == first)
    return tuple(remaining[start:] + remaining[:start])


def call_with_key_rotation(
    provider: str,
    operation: Callable[[str], _RESULT],
    *,
    endpoint: str,
    payload: object = None,
    credentials: Sequence[ProviderCredential] | None = None,
) -> _RESULT:
    """Fail over keys immediately, then retry complete key rounds with bounded backoff."""
    key_ring = tuple(credentials) if credentials is not None else _ordered_credentials(provider)
    if not key_ring:
        raise RuntimeError(f"No {provider} credentials configured.")

    last_error: Exception | None = None
    last_key_id = key_ring[0].key_id
    last_diagnostics = ""
    round_failures = 0
    delays: tuple[int | None, ...] = (*PROVIDER_RETRY_BACKOFF_SECONDS, None)

    for delay_after_round in delays:
        round_only_daily_quota = True
        for credential in key_ring:
            last_key_id = credential.key_id
            try:
                result = operation(credential.value)
                if round_failures:
                    log_connection_event(
                        "provider_key_failover_succeeded",
                        endpoint=endpoint,
                        payload={
                            "provider": provider,
                            "key_id": credential.key_id,
                            "previous_rate_limit_failures": round_failures,
                        },
                    )
                return result
            except Exception as exc:
                if not is_rate_limit_error(exc):
                    raise
                last_error = exc
                if not _is_daily_quota_exhausted(exc):
                    round_only_daily_quota = False
                round_failures += 1
                status_code = _error_status(exc)
                last_diagnostics = log_connection_failure(
                    exc,
                    endpoint=endpoint,
                    payload={
                        "provider": provider,
                        "key_id": credential.key_id,
                        "status_code": status_code,
                        "attempt": round_failures,
                        "operation": "rate_limit_failover",
                        "request": payload,
                    },
                )

        if delay_after_round is None:
            break
        if round_only_daily_quota:
            break
        time.sleep(delay_after_round)

    if last_error is None:
        raise RuntimeError(f"{provider} request failed without a provider response.")
    # When every key in the ring refuses, the API also sends a plain-text 429 body, so
    # the real "quota exceeded" detail is only in the last raw exception.
    raise ProviderRateLimitError(
        provider=provider,
        key_id=last_key_id,
        cooldown_seconds=_suggested_cooldown(last_error),
        status_code=_retryable_status(last_error),
        diagnostics=last_diagnostics,
        raw_detail=_quota_summary(last_error),
    ) from last_error


def gemini_model_chain() -> tuple[str, ...]:
    """Preferred Gemini model first, then configured fallbacks (deduplicated)."""
    chain: list[str] = []
    for model in (GEMINI_MODEL, *GEMINI_MODEL_FALLBACKS):
        if model and model not in chain:
            chain.append(model)
    return tuple(chain)


class EmptyModelResponse(RuntimeError):
    """The provider answered 200 but returned no usable text."""


def call_gemini_model(
    build_operation: Callable[[str, str], _RESULT],
    *,
    endpoint: str,
    payload: object = None,
    credentials: Sequence[ProviderCredential] | None = None,
    text_of: Callable[[_RESULT], str | None] | None = None,
) -> tuple[_RESULT, str]:
    """Run ``build_operation(api_key, model)`` across the model chain and the key ring.

    Google retires Gemini Flash models without notice, so a 404 must move on to the
    next model in the chain instead of failing the request. Free-tier quota is also
    granted per (project, model), so a model that is exhausted across every key does
    not mean the others are. Rate limits still fail over between keys within a model
    before the model itself is skipped.

    Pass ``text_of`` to treat an empty completion as a miss as well. Returns
    ``(result, model_that_answered)``.
    """
    last_error: Exception | None = None
    chain = gemini_model_chain()
    for index, model in enumerate(chain):
        last_model = index == len(chain) - 1
        try:
            result = call_with_key_rotation(
                "Gemini",
                lambda key, _model=model: build_operation(key, _model),
                endpoint=f"{endpoint}:{model}",
                payload=payload,
                credentials=credentials,
            )
        except Exception as exc:
            exhausted = isinstance(exc, ProviderRateLimitError) or is_retired_model_error(exc)
            if not exhausted or last_model:
                raise
            last_error = exc
            log_connection_failure(
                exc,
                endpoint=f"{endpoint}:{model}",
                payload={
                    "provider": "Gemini",
                    "model": model,
                    "operation": "model_unavailable_skip",
                    "retired": is_retired_model_error(exc),
                },
            )
            continue

        # A 200 with no text is indistinguishable from success to the caller, and the
        # UI renders it as a permanent "thinking" state. Treat it as a miss and try the
        # next model instead of returning an empty answer.
        if text_of is not None and not str(text_of(result) or "").strip():
            exc = EmptyModelResponse(f"{model} returned an empty response.")
            if last_model:
                raise exc
            last_error = exc
            log_connection_failure(
                exc,
                endpoint=f"{endpoint}:{model}",
                payload={"provider": "Gemini", "model": model, "operation": "empty_response_skip"},
            )
            continue

        return result, model

    raise RuntimeError(
        f"No configured Gemini model returned an answer. Tried: {', '.join(chain)}."
    ) from last_error


def call_gemini_with_fallback(
    operation: Callable[[str], _RESULT],
    *,
    endpoint: str,
    payload: object = None,
    credentials: Sequence[ProviderCredential] | None = None,
) -> _RESULT:
    """Run a Gemini operation with ordered key failover and quota-aware retries."""
    return call_with_key_rotation(
        "Gemini",
        operation,
        endpoint=endpoint,
        payload=payload,
        credentials=credentials,
    )
