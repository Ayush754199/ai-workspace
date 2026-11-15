"""Static provider/model registry and its merge with the database overrides.

Two sources describe which routes the gateway may use:

* the static :data:`MODEL_REGISTRY` below -- code-owned, always present, and the
  only source when ``provider_models`` is empty (a fresh database);
* the ``providers`` / ``provider_models`` tables -- an administrator can add,
  disable or re-verify a model without a deploy.

:func:`merge_registry` combines them by ``(provider, model)`` with the database
winning for any key it defines. A partially populated table therefore hides only
the exact rows it manages, instead of discarding every static provider (which is
what made a fresh or half-seeded database report "no eligible provider" even with
valid keys configured).

Readiness is decided by the environment, never by the table alone: a route is
usable only when its adapter can actually be constructed from configured
credentials (see ``AIGateway.eligible_routes``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.core import models as model_config


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    model: str
    enabled: bool
    free_eligible: bool | None
    capabilities: frozenset[str]
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    availability: str = "unknown"
    verified_at: datetime | None = None
    restrictions: tuple[str, ...] = ()
    # ``static`` entries came from code; the router treats them identically but
    # diagnostics can say where a route was configured.
    source: str = "static"

    @property
    def free(self) -> bool:
        """Compatibility view; only verified true is eligible."""
        return self.free_eligible is True

    @property
    def verified_free(self) -> bool:
        return self.enabled and self.free_eligible is True and self.availability == "available" and self.verified_at is not None

    @property
    def key(self) -> tuple[str, str]:
        return (self.provider, self.model)


def _static(*, provider: str, model: str, enabled: bool, free_eligible: bool | None, capabilities: set[str], **kwargs) -> ModelSpec:
    return ModelSpec(provider, model, enabled, free_eligible, frozenset(capabilities), source="static", **kwargs)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def build_static_registry() -> tuple[ModelSpec, ...]:
    """The code-owned registry, resolved from the central model configuration.

    ``openrouter/free`` is a provider-side router (OpenRouter chooses a currently
    available free model per request), so it is a stable id. Paid or unverified
    models are not eligible merely because a client sends their name.
    """
    entries: list[ModelSpec] = [
        _static(
            provider="mock", model="mock-chat", enabled=True, free_eligible=True,
            capabilities={"chat", "test"}, availability="available", verified_at=_now(),
        ),
    ]
    openrouter = model_config.provider_model("openrouter")
    if openrouter.configured:
        entries.append(
            _static(
                provider="openrouter", model=openrouter.model, enabled=True, free_eligible=True,
                capabilities={"chat"}, availability="available", verified_at=_now(),
            )
        )
    groq = model_config.provider_model("groq")
    entries.append(
        _static(
            provider="groq", model=groq.model, enabled=groq.configured, free_eligible=True if groq.configured else None,
            capabilities={"chat"},
            availability="available" if groq.configured else "unknown",
            verified_at=_now() if groq.configured else None,
            restrictions=() if groq.configured else ("set GROQ_MODEL to an account you have verified",),
        )
    )
    gemini = model_config.provider_model("gemini")
    entries.append(
        _static(
            provider="gemini", model=gemini.model, enabled=True, free_eligible=True,
            capabilities={"chat"}, availability="available", verified_at=_now(),
            # The adapter rotates through the fallback chain when the primary is
            # retired or quota-limited; the list is recorded for diagnostics only.
            restrictions=(f"fallbacks: {', '.join(gemini.fallbacks)}",) if gemini.fallbacks else (),
        )
    )
    huggingface = model_config.provider_model("huggingface")
    entries.append(
        _static(
            provider="huggingface", model=huggingface.model or "configured-free-model",
            enabled=huggingface.configured, free_eligible=None, capabilities={"chat", "image"},
            restrictions=("requires configured endpoint verification",),
        )
    )
    entries.extend(
        [
            ModelSpec("muse_spark", "configured-muse-spark", False, None, frozenset({"chat", "research", "multimodal"}), restrictions=("requires private endpoint, credentials, and admin verification",)),
            ModelSpec("muse_image", "configured-muse-image", False, None, frozenset({"image"}), restrictions=("requires private image endpoint, credentials, and admin verification",)),
            ModelSpec("cloudflare", "configured-workers-ai", False, None, frozenset({"research", "image"}), restrictions=("requires account quota and model verification",)),
        ]
    )
    return tuple(entries)


MODEL_REGISTRY = build_static_registry()


def spec_from_db_row(model, provider, *, source: str = "database") -> ModelSpec:
    """Project one ``provider_models`` join row onto the registry type."""
    return ModelSpec(
        "huggingface" if provider.name.casefold().replace(" ", "") == "huggingface" else provider.name.casefold(),
        model.model,
        model.enabled and provider.enabled,
        model.is_free,
        frozenset(model.capabilities or []),
        max_input_tokens=getattr(model, "max_input_tokens", None),
        max_output_tokens=getattr(model, "max_output_tokens", None),
        availability=getattr(model, "availability", "unknown") or "unknown",
        verified_at=getattr(model, "verified_at", None),
        restrictions=tuple(getattr(model, "restrictions", None) or ()),
        source=source,
    )


def merge_registry(static: tuple[ModelSpec, ...], overrides: tuple[ModelSpec, ...]) -> tuple[ModelSpec, ...]:
    """Union of both sources; a database row fully replaces the static entry."""

    merged: dict[tuple[str, str], ModelSpec] = {}
    for spec in static:
        merged.setdefault(spec.key, spec)
    for spec in overrides:
        # A database row must be able to disable a statically enabled model, so it
        # always wins for its key -- including when ``enabled`` is False.
        merged[spec.key] = spec
    # Provider-level disablement is a tombstone too. A disabled provider can have
    # only disabled models in the table; all those rows still replace their static
    # counterparts by key. Other providers remain untouched.
    return tuple(merged.values())


def models_for(provider: str, registry: tuple[ModelSpec, ...] | None = None) -> tuple[ModelSpec, ...]:
    return tuple(item for item in (registry or MODEL_REGISTRY) if item.provider == provider)
