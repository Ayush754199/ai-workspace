"""One place that resolves the model identifiers used by the AI gateway.

Before this module the same model id existed in three unrelated places:

* ``app/core/config.py``       -- ``GEMINI_MODEL``, ``OPENROUTER_MODEL``, ``GROQ_MODEL``
* ``config.py`` (repo root)    -- ``GEMINI_MODEL`` + ``GEMINI_MODEL_FALLBACKS`` (Streamlit)
* ``ai_gateway/registry.py``   -- the literal ``"gemini-3.8-flash"`` in a static entry

Nothing here invents a model. A provider only receives an id when the environment
already names one; otherwise its route stays disabled with a reason an operator can
act on. The values themselves keep their existing defaults so behaviour does not
change when a variable is unset.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderModelConfig:
    provider: str
    model: str
    # Fallback ids tried in order when the primary is retired or rate-limited.
    fallbacks: tuple[str, ...] = ()

    @property
    def configured(self) -> bool:
        return bool(self.model.strip())


def _settings():
    from app.core.config import settings

    return settings


def _root_gemini_fallbacks() -> tuple[str, ...]:
    """Reuse the Streamlit-side fallback chain instead of duplicating it.

    ``config.py`` stays the owner of the Gemini fallback list because
    ``backend/key_rotator.py`` already imports it for the rotating client.
    """
    try:
        from config import GEMINI_MODEL_FALLBACKS

        return tuple(str(item).strip() for item in GEMINI_MODEL_FALLBACKS if str(item).strip())
    except Exception:  # noqa: BLE001 - the API must run without the root module
        return ()


def openrouter_model() -> str:
    return _settings().OPENROUTER_MODEL.strip()


def gemini_model() -> str:
    return _settings().GEMINI_MODEL.strip()


def gemini_fallbacks() -> tuple[str, ...]:
    return tuple(
        model for model in _root_gemini_fallbacks() if model and model != gemini_model()
    )


def groq_model() -> str:
    """Groq has no repository default: the account/model must be chosen explicitly."""
    return (_settings().GROQ_MODEL or "").strip()


def huggingface_model() -> str:
    return (_settings().HF_TEXT_MODEL or "").strip()


def provider_model(provider: str) -> ProviderModelConfig:
    name = provider.strip().lower()
    if name == "openrouter":
        return ProviderModelConfig("openrouter", openrouter_model())
    if name == "gemini":
        return ProviderModelConfig("gemini", gemini_model(), gemini_fallbacks())
    if name == "groq":
        return ProviderModelConfig("groq", groq_model())
    if name in {"huggingface", "hugging face"}:
        return ProviderModelConfig("huggingface", huggingface_model())
    return ProviderModelConfig(name, "")


def known_models() -> dict[str, str]:
    """Non-secret snapshot used by diagnostics/tests (never includes credentials)."""
    return {
        "openrouter": openrouter_model(),
        "gemini": gemini_model(),
        "groq": groq_model(),
        "huggingface": huggingface_model(),
    }
