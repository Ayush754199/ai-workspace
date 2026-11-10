from __future__ import annotations

import requests
from dataclasses import dataclass

from backend.key_rotator import (
    ProviderRateLimitError,
    call_gemini_model,
    call_with_key_rotation,
    get_provider_credentials,
)


class GatewayProviderError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, retryable: bool = False, uncertain: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.uncertain = uncertain


@dataclass(frozen=True)
class GatewayResponse:
    text: str
    provider: str
    model: str
    credential_id: str | None = None


class MockProvider:
    name = "mock"

    def generate(self, prompt: str, *, system_prompt: str | None = None, model: str | None = None) -> str:
        return f"[mock:{model}] {prompt.strip()}"

    def stream(self, prompt: str, *, system_prompt: str | None = None, model: str = "mock-chat"):
        text = self.generate(prompt, system_prompt=system_prompt, model=model)
        for index in range(0, len(text), 12):
            yield text[index:index + 12]


class OpenRouterProvider:
    name = "openrouter"

    def __init__(self, credentials, model: str, *, base_url: str = "https://openrouter.ai/api/v1", timeout: float = 120) -> None:
        # Accept a single-key form for callers/tests, while production uses
        # the numbered OPENROUTER_KEY_n pool from backend/.env.
        if isinstance(credentials, str):
            credentials = (type("Credential", (), {"value": credentials, "key_id": "OPENROUTER_KEY"})(),)
        self.credentials = tuple(credentials)
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def generate(self, prompt: str, *, system_prompt: str | None = None, model: str | None = None) -> str:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        payload = {"model": model or self.model, "messages": messages}

        def request(api_key: str):
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            choices = response.json().get("choices") or []
            answer = (choices[0].get("message") or {}).get("content") if choices else None
            if not isinstance(answer, str) or not answer.strip():
                raise GatewayProviderError("OpenRouter returned an empty response")
            return answer.strip()

        try:
            return call_with_key_rotation(
                "OpenRouter", request,
                endpoint=f"{self.base_url}/chat/completions",
                payload={"model": payload["model"]},
                credentials=self.credentials,
            )
        except ProviderRateLimitError as exc:
            raise GatewayProviderError(
                "OpenRouter free-model rate limit or capacity exhausted",
                status_code=exc.status_code, retryable=True,
            ) from exc
        except requests.Timeout as exc:
            raise GatewayProviderError("OpenRouter request timed out", uncertain=True) from exc
        except requests.ConnectionError as exc:
            raise GatewayProviderError("OpenRouter connection failed", uncertain=True) from exc
        except requests.HTTPError as exc:
            status = getattr(exc.response, "status_code", None)
            raise GatewayProviderError("OpenRouter rejected the request", status_code=status) from exc
        except requests.JSONDecodeError as exc:
            # A 200 with a non-JSON body must surface as a provider failure, not
            # as an unhandled decoder exception underneath the API.
            raise GatewayProviderError("OpenRouter returned a malformed response") from exc


class GeminiGatewayProvider:
    """Common gateway adapter over the existing Gemini key/model rotation."""

    name = "gemini"

    def __init__(self, model: str, credentials=None, timeout: float | None = None) -> None:
        self.model = model
        self.timeout = timeout
        self.credentials = tuple(credentials) if credentials is not None else get_provider_credentials("Gemini")
        if not self.credentials:
            raise GatewayProviderError("Gemini credentials are not configured")

    def generate(self, prompt: str, *, system_prompt: str | None = None, model: str | None = None) -> GatewayResponse:
        selected_model = model or self.model
        contents = f"{system_prompt}\n\nUser: {prompt}" if system_prompt else prompt

        def operation(key: str, requested_model: str):
            from google import genai
            client = genai.Client(api_key=key)
            try:
                request_options = {"timeout": self.timeout} if self.timeout else {}
                return client.models.generate_content(model=requested_model, contents=contents, **request_options)
            finally:
                close = getattr(client, "close", None)
                if callable(close):
                    close()

        try:
            result = call_with_key_rotation(
                "Gemini",
                lambda key: operation(key, selected_model),
                endpoint="Gemini/gateway",
                payload={"model": selected_model},
                credentials=self.credentials,
            )
        except ProviderRateLimitError as exc:
            raise GatewayProviderError(
                "Gemini rate limit or quota exhausted",
                status_code=exc.status_code,
                retryable=True,
            ) from exc
        except (TimeoutError, requests.Timeout) as exc:
            raise GatewayProviderError("Gemini request timed out", uncertain=True) from exc
        except Exception as exc:
            status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
            raise GatewayProviderError("Gemini request failed", status_code=status, uncertain=isinstance(exc, ConnectionError)) from exc
        text = str(getattr(result, "text", "") or "").strip()
        if not text:
            raise GatewayProviderError("Gemini returned an empty response")
        return GatewayResponse(text, self.name, selected_model)


class HuggingFaceGatewayProvider:
    """Adapter for the existing Hugging Face router chat-completions path."""

    name = "huggingface"

    def __init__(self, endpoint: str, model: str, credentials=None, timeout: float = 90.0) -> None:
        self.endpoint = endpoint
        self.model = model
        self.credentials = tuple(credentials) if credentials is not None else get_provider_credentials("Hugging Face")
        self.timeout = timeout
        if not self.credentials:
            raise GatewayProviderError("Hugging Face credentials are not configured")

    def generate(self, prompt: str, *, system_prompt: str | None = None, model: str | None = None) -> GatewayResponse:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        payload = {"model": model or self.model, "messages": messages, "max_tokens": 512}

        def operation(token: str):
            response = requests.post(
                self.endpoint,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
            answer = None
            if isinstance(data, dict):
                choices = data.get("choices") or []
                if choices:
                    answer = (choices[0].get("message") or {}).get("content") or choices[0].get("text")
                answer = answer or data.get("generated_text") or data.get("text")
            elif isinstance(data, list) and data and isinstance(data[0], dict):
                answer = data[0].get("generated_text")
            if not isinstance(answer, str) or not answer.strip():
                raise GatewayProviderError("Hugging Face returned an empty response")
            return answer.strip()

        try:
            answer = call_with_key_rotation(
                "Hugging Face",
                operation,
                endpoint=self.endpoint,
                payload={"model": payload["model"]},
                credentials=self.credentials,
            )
        except ProviderRateLimitError as exc:
            raise GatewayProviderError("Hugging Face rate limit or capacity exhausted", status_code=exc.status_code, retryable=True) from exc
        except requests.Timeout as exc:
            raise GatewayProviderError("Hugging Face request timed out", uncertain=True) from exc
        except requests.HTTPError as exc:
            status = getattr(exc.response, "status_code", None)
            raise GatewayProviderError("Hugging Face endpoint rejected the request", status_code=status) from exc
        except GatewayProviderError:
            raise
        except Exception as exc:
            raise GatewayProviderError("Hugging Face request failed", uncertain=isinstance(exc, ConnectionError)) from exc
        return GatewayResponse(answer, self.name, model or self.model)


class GroqGatewayProvider:
    """Groq chat-completions adapter (OpenAI-compatible schema, free-tier keys)."""

    name = "groq"

    def __init__(self, endpoint: str, model: str, credentials=None, timeout: float = 90.0) -> None:
        self.endpoint = endpoint
        self.model = model
        self.timeout = timeout
        self.credentials = (
            tuple(credentials)
            if credentials is not None
            else get_provider_credentials("Groq")
        )
        if not self.credentials:
            raise GatewayProviderError("Groq credentials are not configured")

    def generate(self, prompt: str, *, system_prompt: str | None = None, model: str | None = None) -> GatewayResponse:
        selected = model or self.model
        if not selected:
            raise GatewayProviderError("No Groq model has been verified by an administrator")
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        payload = {"model": selected, "messages": messages}

        def operation(token: str):
            response = requests.post(
                self.endpoint,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
            choices = data.get("choices") or []
            answer = (choices[0].get("message") or {}).get("content") if choices else None
            if not isinstance(answer, str) or not answer.strip():
                raise GatewayProviderError("Groq returned an empty response")
            return answer.strip()

        try:
            answer = call_with_key_rotation(
                "Groq",
                operation,
                endpoint=self.endpoint,
                payload={"model": selected},
                credentials=self.credentials,
            )
        except ProviderRateLimitError as exc:
            raise GatewayProviderError("Groq rate limit or daily quota exhausted", status_code=exc.status_code, retryable=True) from exc
        except requests.Timeout as exc:
            raise GatewayProviderError("Groq request timed out", uncertain=True) from exc
        except requests.HTTPError as exc:
            status = getattr(exc.response, "status_code", None)
            retryable = status in {408, 429, 500, 502, 503, 504}
            raise GatewayProviderError("Groq endpoint rejected the request", status_code=status, retryable=retryable) from exc
        except GatewayProviderError:
            raise
        except Exception as exc:
            raise GatewayProviderError("Groq request failed", uncertain=isinstance(exc, ConnectionError)) from exc
        return GatewayResponse(answer, self.name, selected)


class MuseSparkGatewayProvider:
    """OpenAI-compatible Muse Spark adapter; endpoint and model are operator-supplied."""

    name = "muse_spark"

    def __init__(self, endpoint: str, api_key: str, model: str, *, timeout: float = 120.0) -> None:
        if not endpoint or not api_key or not model:
            raise GatewayProviderError("Muse Spark is not fully configured")
        self.endpoint, self.api_key, self.model, self.timeout = endpoint.rstrip("/"), api_key, model, timeout

    def generate(self, prompt: str, *, system_prompt: str | None = None, model: str | None = None) -> GatewayResponse:
        messages = ([{"role": "system", "content": system_prompt}] if system_prompt else []) + [{"role": "user", "content": prompt}]
        try:
            response = requests.post(self.endpoint, headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}, json={"model": model or self.model, "messages": messages}, timeout=self.timeout)
            response.raise_for_status()
            choices = response.json().get("choices") or []
            answer = (choices[0].get("message") or {}).get("content") if choices else None
        except requests.Timeout as exc:
            raise GatewayProviderError("Muse Spark request timed out", uncertain=True) from exc
        except requests.ConnectionError as exc:
            raise GatewayProviderError("Muse Spark connection failed", uncertain=True) from exc
        except requests.HTTPError as exc:
            status = getattr(exc.response, "status_code", None)
            raise GatewayProviderError("Muse Spark rejected the request", status_code=status, retryable=status in {408, 409, 429, 500, 502, 503, 504}) from exc
        except (ValueError, requests.JSONDecodeError) as exc:
            raise GatewayProviderError("Muse Spark returned malformed JSON") from exc
        if not isinstance(answer, str) or not answer.strip():
            raise GatewayProviderError("Muse Spark returned an empty response")
        return GatewayResponse(answer.strip(), self.name, model or self.model)


class CloudflareGatewayProvider:
    """Workers AI OpenAI-compatible text adapter for research synthesis."""

    name = "cloudflare"

    def __init__(self, account_id: str, api_token: str, model: str, *, timeout: float = 120.0) -> None:
        if not account_id or not api_token or not model:
            raise GatewayProviderError("Cloudflare Workers AI research is not fully configured")
        self.endpoint = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1/chat/completions"
        self.api_token, self.model, self.timeout = api_token, model, timeout

    def generate(self, prompt: str, *, system_prompt: str | None = None, model: str | None = None) -> GatewayResponse:
        messages = ([{"role": "system", "content": system_prompt}] if system_prompt else []) + [{"role": "user", "content": prompt}]
        try:
            response = requests.post(
                self.endpoint,
                headers={"Authorization": f"Bearer {self.api_token}", "Content-Type": "application/json"},
                json={"model": model or self.model, "messages": messages},
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
            choices = payload.get("choices") or []
            answer = (choices[0].get("message") or {}).get("content") if choices else None
            if not answer:
                answer = ((payload.get("result") or {}).get("response"))
        except requests.Timeout as exc:
            raise GatewayProviderError("Cloudflare Workers AI request timed out", uncertain=True) from exc
        except requests.ConnectionError as exc:
            raise GatewayProviderError("Cloudflare Workers AI connection failed", uncertain=True) from exc
        except requests.HTTPError as exc:
            status = getattr(exc.response, "status_code", None)
            raise GatewayProviderError("Cloudflare Workers AI rejected the request", status_code=status, retryable=status in {408, 409, 429, 500, 502, 503, 504}) from exc
        except (ValueError, requests.JSONDecodeError) as exc:
            raise GatewayProviderError("Cloudflare Workers AI returned malformed JSON") from exc
        if not isinstance(answer, str) or not answer.strip():
            raise GatewayProviderError("Cloudflare Workers AI returned an empty response")
        return GatewayResponse(answer.strip(), self.name, model or self.model)


def _iter_sse_json(response) -> "object":
    """Yield decoded JSON objects from an ``iter_lines`` SSE response."""
    import json as _json

    for raw in response.iter_lines(decode_unicode=True):
        if not raw:
            continue
        line = raw.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            yield _json.loads(payload)
        except ValueError:
            continue


def _stream_mock(prompt: str, model: str):
    text = MockProvider().generate(prompt, model=model)
    for index in range(0, len(text), 12):
        yield text[index:index + 12]


def _stream_openai_compatible(endpoint: str, headers: dict, payload: dict, timeout: float):
    """Shared SSE reader for Groq / OpenRouter / HF router (OpenAI schema)."""
    import json as _json

    payload = {**payload, "stream": True}
    with requests.post(endpoint, headers=headers, json=payload, timeout=timeout, stream=True) as response:
        if response.status_code == 401:
            raise GatewayProviderError("Provider rejected the API key", status_code=401)
        if response.status_code == 429:
            raise GatewayProviderError("Provider rate limit reached", status_code=429, retryable=True)
        if response.status_code >= 400:
            raise GatewayProviderError("Provider rejected the streaming request", status_code=response.status_code)
        for event in _iter_sse_json(response):
            if "error" in event:
                raise GatewayProviderError(str(event.get("error")))
            for choice in event.get("choices") or []:
                delta = choice.get("delta") or {}
                piece = delta.get("content")
                if piece:
                    yield piece


def _mock_stream(*args, **kwargs):
    yield from _stream_mock("", "mock-chat")


MockProvider.stream = lambda self, prompt, *, system_prompt=None, model="mock-chat": _stream_mock(prompt, model)


def _groq_stream(self, prompt: str, *, system_prompt: str | None = None, model: str | None = None):
    selected = model or self.model
    if not selected:
        raise GatewayProviderError("No Groq model has been verified by an administrator")
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    last_error = None
    for credential in self.credentials:
        try:
            yield from _stream_openai_compatible(
                self.endpoint,
                {"Authorization": f"Bearer {credential.value}", "Content-Type": "application/json", "Accept": "text/event-stream"},
                {"model": selected, "messages": messages},
                self.timeout,
            )
            return
        except GatewayProviderError as exc:
            last_error = exc
            if not exc.retryable:
                raise
    raise last_error or GatewayProviderError("Groq streaming failed")


GroqGatewayProvider.stream = _groq_stream


def _openrouter_stream(self, prompt: str, *, system_prompt: str | None = None, model: str | None = None):
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    last_error = None
    for credential in self.credentials:
        try:
            yield from _stream_openai_compatible(
                f"{self.base_url}/chat/completions",
                {"Authorization": f"Bearer {credential.value}", "Content-Type": "application/json"},
                {"model": model or self.model, "messages": messages},
                120,
            )
            return
        except GatewayProviderError as exc:
            last_error = exc
            # Free-model capacity errors may arrive under statuses other than 429.
            # Try the other configured key on transient server responses too.
            if not exc.retryable and exc.status_code not in {408, 429, 500, 502, 503, 504}:
                raise
    raise last_error or GatewayProviderError("OpenRouter streaming failed")


OpenRouterProvider.stream = _openrouter_stream


def _gemini_stream(self, prompt: str, *, system_prompt: str | None = None, model: str | None = None):
    from google import genai

    selected = model or self.model
    contents = f"{system_prompt}\n\nUser: {prompt}" if system_prompt else prompt
    last_error: Exception | None = None
    for credential in self.credentials:
        produced = False
        client = genai.Client(api_key=credential.value)
        try:
            for chunk in client.models.generate_content_stream(model=selected, contents=contents):
                piece = getattr(chunk, "text", None)
                if piece:
                    produced = True
                    yield piece
            return
        except Exception as exc:  # noqa: BLE001 - mapped to a gateway error below
            last_error = exc
            if produced:
                raise GatewayProviderError("Gemini stream interrupted", uncertain=True) from exc
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()
    raise GatewayProviderError(f"Gemini streaming failed: {last_error}") from last_error


GeminiGatewayProvider.stream = _gemini_stream
