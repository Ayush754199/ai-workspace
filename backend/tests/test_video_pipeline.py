import sys
from types import ModuleType

import pytest

from backend import key_rotator, video_provider
from backend.key_rotator import (
    ProviderCredential,
    ProviderRateLimitError,
    call_gemini_with_fallback,
    call_with_key_rotation,
    get_next_gemini_key,
    get_next_hf_token,
)
from backend.video_provider import _parse_scenes


def test_hugging_face_keys_rotate_and_refresh(monkeypatch):
    monkeypatch.delenv("HF_TOKENS", raising=False)
    monkeypatch.delenv("HF_API_TOKEN", raising=False)
    monkeypatch.setenv("HF_TOKEN_1", "first-token")
    monkeypatch.setenv("HF_TOKEN_2", "second-token")

    assert [get_next_hf_token() for _ in range(3)] == [
        "first-token",
        "second-token",
        "first-token",
    ]

    monkeypatch.setenv("HF_TOKEN_1", "updated-token")
    assert get_next_hf_token() == "updated-token"


def test_gemini_keys_rotate(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    for index in range(1, 17):
        monkeypatch.delenv(f"GEMINI_KEY_{index}", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY_1", "first-key")
    monkeypatch.setenv("GEMINI_API_KEY_2", "second-key")

    assert [get_next_gemini_key() for _ in range(3)] == [
        "first-key",
        "second-key",
        "first-key",
    ]


def test_gemini_key_slots_are_loaded_in_numeric_order_and_preferred(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEYS", '["legacy-array-key"]')
    monkeypatch.setenv("GEMINI_API_KEY_1", "legacy-numbered-key")
    for index in range(1, 17):
        monkeypatch.setenv(f"GEMINI_KEY_{index}", f"canonical-key-{index}")
    monkeypatch.setenv("GEMINI_KEY_17", "ignored-key")

    credentials = key_rotator.get_provider_credentials("Gemini")

    assert len(credentials) == 16
    assert [credential.key_id for credential in credentials] == [
        f"GEMINI_KEY_{index}" for index in range(1, 17)
    ]


def test_gemini_key_slots_ignore_empty_values_and_preserve_legacy_fallback(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    for index in range(1, 17):
        monkeypatch.delenv(f"GEMINI_KEY_{index}", raising=False)
    monkeypatch.setenv("GEMINI_KEY_2", "canonical-key")
    monkeypatch.setenv("GEMINI_API_KEY_1", "legacy-key")
    credentials = key_rotator.get_provider_credentials("Gemini")
    assert [(credential.value, credential.key_id) for credential in credentials] == [
        ("canonical-key", "GEMINI_KEY_2")
    ]

    for index in range(1, 17):
        monkeypatch.delenv(f"GEMINI_KEY_{index}", raising=False)
    credentials = key_rotator.get_provider_credentials("Gemini")
    assert [(credential.value, credential.key_id) for credential in credentials] == [
        ("legacy-key", "GEMINI_API_KEY_1")
    ]


def test_gemini_fallback_visits_all_sixteen_keys_in_order(monkeypatch):
    attempts = []
    sleeps = []

    class RateLimited(Exception):
        status_code = 429

    credentials = tuple(
        ProviderCredential(f"key-{index}", f"GEMINI_KEY_{index}")
        for index in range(1, 17)
    )
    monkeypatch.setattr(key_rotator.time, "sleep", sleeps.append)
    monkeypatch.setattr(key_rotator, "log_connection_failure", lambda *args, **kwargs: "{}")
    monkeypatch.setattr(key_rotator, "log_connection_event", lambda *args, **kwargs: "{}")

    def operation(key):
        attempts.append(key)
        if key != "key-16":
            raise RateLimited("per-minute rate limit")
        return "success"

    result = call_gemini_with_fallback(
        operation,
        endpoint="Gemini/test-model",
        credentials=credentials,
    )

    assert result == "success"
    assert attempts == [f"key-{index}" for index in range(1, 17)]
    assert sleeps == []


def test_gemini_daily_quota_checks_all_keys_without_retry_delay(monkeypatch):
    attempts = []
    sleeps = []
    credentials = tuple(
        ProviderCredential(f"key-{index}", f"GEMINI_KEY_{index}")
        for index in range(1, 17)
    )

    class DailyQuotaExceeded(Exception):
        status_code = 429

    monkeypatch.setattr(key_rotator.time, "sleep", sleeps.append)
    monkeypatch.setattr(key_rotator, "log_connection_failure", lambda *args, **kwargs: "{}")

    def operation(key):
        attempts.append(key)
        raise DailyQuotaExceeded(
            "RESOURCE_EXHAUSTED: requests per day limit exceeded, limit: 0. "
            "Please retry in 2h."
        )

    with pytest.raises(ProviderRateLimitError) as raised:
        call_gemini_with_fallback(
            operation,
            endpoint="Gemini/test-model",
            credentials=credentials,
        )

    assert attempts == [f"key-{index}" for index in range(1, 17)]
    assert sleeps == []
    assert raised.value.key_id == "GEMINI_KEY_16"
    assert raised.value.cooldown_seconds == 7200


@pytest.mark.parametrize("status_code", [429, 503])
def test_gemini_fallback_retries_full_key_ring_with_exponential_backoff(
    monkeypatch, status_code
):
    attempts = []
    sleeps = []
    credentials = tuple(
        ProviderCredential(f"key-{index}", f"GEMINI_KEY_{index}")
        for index in range(1, 17)
    )

    class TransientProviderError(Exception):
        pass

    monkeypatch.setattr(key_rotator.time, "sleep", sleeps.append)
    monkeypatch.setattr(key_rotator, "log_connection_failure", lambda *args, **kwargs: "{}")

    def operation(key):
        attempts.append(key)
        error = TransientProviderError("temporary provider limit")
        error.status_code = status_code
        raise error

    with pytest.raises(ProviderRateLimitError):
        call_gemini_with_fallback(
            operation,
            endpoint="Gemini/test-model",
            credentials=credentials,
        )

    key_order = [f"key-{index}" for index in range(1, 17)]
    assert attempts == key_order * 4
    assert sleeps == [2, 4, 8]


def test_key_rotation_fails_over_before_backoff(monkeypatch):
    sleeps = []
    attempted_keys = []

    class TooManyRequests(Exception):
        status_code = 429

    monkeypatch.setattr(key_rotator.time, "sleep", sleeps.append)
    monkeypatch.setattr(key_rotator, "log_connection_failure", lambda *args, **kwargs: "safe diagnostics")
    monkeypatch.setattr(key_rotator, "log_connection_event", lambda *args, **kwargs: "{}")

    def operation(key):
        attempted_keys.append(key)
        if key == "key-a":
            raise TooManyRequests("quota exceeded")
        return "ok"

    result = call_with_key_rotation(
        "Gemini",
        operation,
        endpoint="Gemini/test-model",
        credentials=(
            ProviderCredential("key-a", "GEMINI_API_KEYS[1]"),
            ProviderCredential("key-b", "GEMINI_API_KEYS[2]"),
        ),
    )

    assert result == "ok"
    assert attempted_keys == ["key-a", "key-b"]
    assert sleeps == []


def test_hugging_face_video_generation_fails_over_to_backup_token(monkeypatch):
    attempts = []

    class RateLimited(Exception):
        status_code = 429

    class FakeClient:
        # Signature mirrors the installed gradio_client (2.x): token is positional-or-keyword.
        def __init__(self, _space, token=None):
            self.token = token

        def predict(self, *_inputs, api_name=None):
            attempts.append(self.token)
            if self.token == "primary":
                raise RateLimited("too many requests")
            return b"video bytes"

        def close(self):
            pass

    gradio_client = ModuleType("gradio_client")
    gradio_client.Client = FakeClient
    monkeypatch.setitem(sys.modules, "gradio_client", gradio_client)
    monkeypatch.setenv("HF_TOKENS", '["primary", "backup"]')
    monkeypatch.setattr(video_provider, "HF_VIDEO_API_NAME", None)
    monkeypatch.setattr(video_provider, "HF_VIDEO_INPUTS_JSON", '["{prompt}"]')
    monkeypatch.delenv("HF_TOKEN_1", raising=False)
    monkeypatch.delenv("HF_TOKEN_2", raising=False)
    monkeypatch.delenv("HF_API_TOKEN", raising=False)
    monkeypatch.setattr(key_rotator, "log_connection_failure", lambda *args, **kwargs: "{}")
    monkeypatch.setattr(key_rotator, "log_connection_event", lambda *args, **kwargs: "{}")
    monkeypatch.setattr(video_provider, "log_connection_failure", lambda *args, **kwargs: "{}")
    monkeypatch.setattr(video_provider, "log_connection_event", lambda *args, **kwargs: "{}")

    assert video_provider.generate_single_clip("a scene") == b"video bytes"
    assert attempts == ["primary", "backup"]


@pytest.mark.parametrize("status_code", [429, 503])
def test_key_rotation_retries_after_exponential_backoff(monkeypatch, status_code):
    sleeps = []
    attempts = 0

    class ProviderBusy(Exception):
        def __init__(self):
            self.status_code = status_code

    monkeypatch.setattr(key_rotator.time, "sleep", sleeps.append)
    monkeypatch.setattr(key_rotator, "log_connection_failure", lambda *args, **kwargs: "safe diagnostics")

    def operation(_key):
        nonlocal attempts
        attempts += 1
        raise ProviderBusy()

    with pytest.raises(ProviderRateLimitError) as raised:
        call_with_key_rotation(
            "Hugging Face",
            operation,
            endpoint="https://huggingface.co/spaces/example/video",
            credentials=(ProviderCredential("token", "HF_TOKENS[1]"),),
        )

    assert attempts == 4
    assert sleeps == [2, 4, 8]
    assert raised.value.key_id == "HF_TOKENS[1]"
    assert raised.value.status_code == status_code
    assert raised.value.cooldown_seconds == 30


def test_daily_quota_exhaustion_fails_over_once_without_backoff(monkeypatch):
    attempts = []
    sleeps = []

    class DailyQuotaExceeded(Exception):
        status_code = 429

    credentials = (
        ProviderCredential("key-a", "GEMINI_API_KEYS[1]"),
        ProviderCredential("key-b", "GEMINI_API_KEYS[2]"),
    )

    def operation(key):
        attempts.append(key)
        raise DailyQuotaExceeded(
            "429 RESOURCE_EXHAUSTED: requests per day limit exceeded, limit: 0. "
            "Please retry in 1h2m3.1s."
        )

    monkeypatch.setattr(key_rotator.time, "sleep", sleeps.append)
    monkeypatch.setattr(key_rotator, "log_connection_failure", lambda *args, **kwargs: "{}")

    with pytest.raises(ProviderRateLimitError) as raised:
        call_with_key_rotation(
            "Gemini",
            operation,
            endpoint="Gemini/test-model",
            credentials=credentials,
        )

    assert attempts == ["key-a", "key-b"]
    assert sleeps == []
    assert raised.value.cooldown_seconds == 3724


def test_key_rotation_logs_safe_key_identifiers_for_quota_exceptions(monkeypatch):
    failures = []

    class QuotaExceeded(Exception):
        pass

    monkeypatch.setattr(key_rotator.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        key_rotator,
        "log_connection_failure",
        lambda error, **kwargs: failures.append((str(error), kwargs["payload"])) or "safe diagnostics",
    )

    def operation(_key):
        raise QuotaExceeded("RESOURCE_EXHAUSTED: project quota exceeded")

    with pytest.raises(ProviderRateLimitError):
        call_with_key_rotation(
            "Gemini",
            operation,
            endpoint="Gemini/test-model",
            credentials=(
                ProviderCredential("first-secret", "GEMINI_API_KEYS[1]"),
                ProviderCredential("second-secret", "GEMINI_API_KEYS[2]"),
            ),
        )

    assert len(failures) == 8
    assert [item[1]["key_id"] for item in failures[:2]] == [
        "GEMINI_API_KEYS[1]",
        "GEMINI_API_KEYS[2]",
    ]
    assert "first-secret" not in repr(failures)
    assert "second-secret" not in repr(failures)


def test_key_arrays_accept_json_and_comma_separated_values(monkeypatch):
    # GEMINI_KEY_n takes precedence over GEMINI_API_KEYS, so clear it for this isolation test.
    for index in range(1, 17):
        monkeypatch.delenv(f"GEMINI_KEY_{index}", raising=False)
    monkeypatch.setenv("GEMINI_API_KEYS", '["alpha", "beta"]')
    monkeypatch.setenv("HF_TOKENS", "first, second")
    monkeypatch.delenv("GEMINI_API_KEY_1", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY_2", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("HF_TOKEN_1", raising=False)
    monkeypatch.delenv("HF_TOKEN_2", raising=False)
    monkeypatch.delenv("HF_API_TOKEN", raising=False)

    assert [
        item.value for item in key_rotator.get_provider_credentials("Gemini", include_numbered=False)
    ] == ["alpha", "beta"]
    assert [
        item.value for item in key_rotator.get_provider_credentials("Hugging Face", include_numbered=False)
    ] == ["first", "second"]


def test_parse_six_numbered_scenes_with_markdown_fences():
    response = "```text\n" + "\n".join(
        f"{index}. Scene {index}: a connected shot" for index in range(1, 7)
    ) + "\n```"

    assert _parse_scenes(response) == [
        f"Scene {index}: a connected shot" for index in range(1, 7)
    ]


def test_parse_scenes_requires_exactly_six():
    with pytest.raises(ValueError, match="exactly 6"):
        _parse_scenes("1. only one scene")
