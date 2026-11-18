from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings
from backend.key_rotator import get_provider_credentials
from sqlalchemy.orm import Session
from .providers import (
    GatewayProviderError,
    CloudflareGatewayProvider,
    GeminiGatewayProvider,
    GroqGatewayProvider,
    HuggingFaceGatewayProvider,
    MuseSparkGatewayProvider,
    MockProvider,
    OpenRouterProvider,
)
from .registry import MODEL_REGISTRY, ModelSpec, merge_registry, spec_from_db_row
from .quota import QuotaService
from app.services.provider_budget import ProviderBudgetError


class GatewayPolicyError(ValueError):
    pass


class GatewayUnavailableError(RuntimeError):
    pass


def _flatten_conversation(conversation: list[dict]) -> str:
    """Render stored turns into one prompt for providers without a message API."""
    lines: list[str] = []
    for turn in conversation[-20:]:
        role = str(turn.get("role", "user")).capitalize()
        content = str(turn.get("content", "")).strip()
        if content:
            lines.append(f"{role}: {content[:4000]}")
    return "\n\n".join(lines)


@dataclass(frozen=True)
class Route:
    provider: str
    model: str
    free: bool = True


class AIGateway:
    """Selects only backend-approved, free-capable routes."""

    def __init__(self, *, registry: tuple[ModelSpec, ...] | None = None) -> None:
        self.mode = settings.AI_GATEWAY_MODE.lower().strip()
        self.registry = registry or MODEL_REGISTRY
        # Model ids that came only from the database (per provider); filled by
        # ``eligible_routes``. Never contains credentials.
        self._db_overrides_model: dict[str, str] = {}
        # Admin-configurable preference order; settings values are the fallback.
        self.order: tuple[str, ...] = tuple(name.strip().lower() for name in settings.FREE_PROVIDERS if name.strip())

    def set_order(self, order: list[str] | tuple[str, ...] | None) -> None:
        if order:
            self.order = tuple(str(name).strip().lower() for name in order if str(name).strip())

    def registry_for(self, db: Session | None = None) -> tuple[ModelSpec, ...]:
        """Static registry merged with the admin's database overrides.

        A fresh database (no ``provider_models`` rows) must not disable chat: the
        static registry is the baseline and the database only overrides it. Before
        Phase 1 an empty or partial table either fell back to an unverified static
        entry or replaced the whole registry, which is why "empty database + a
        valid API key" produced "No eligible free AI provider".
        """
        if db is None:
            return self.registry
        from app.models.gateway import Provider, ProviderModel

        try:
            # Disabled rows are included on purpose: a database row must be able to
            # turn a statically enabled model off, so filtering here would silently
            # re-enable it through the merge.
            rows = (
                db.query(ProviderModel, Provider)
                .join(Provider, Provider.id == ProviderModel.provider_id)
                .all()
            )
        except Exception:  # noqa: BLE001 - a missing table must not kill chat
            return self.registry
        overrides = tuple(
            spec_from_db_row(model, provider)
            for model, provider in rows
            if provider.enabled
        )
        if not overrides:
            return self.registry
        merged = merge_registry(self.registry, overrides)
        # Provider.enabled is a separate admin switch from ProviderModel.enabled.
        # A disabled provider must suppress its static models even if its model rows
        # remain enabled from before the provider was toggled off.
        disabled_providers = {
            provider.name.casefold().replace(" ", "")
            for _model, provider in rows
            if not provider.enabled
        }
        if disabled_providers:
            merged = tuple(
                spec for spec in merged
                if spec.provider.casefold().replace(" ", "") not in disabled_providers
            )
        return merged

    def provider_ready(self, provider: str) -> tuple[bool, str]:
        """Is this provider's credential configuration present right now?

        Every provider is validated the same way -- through the environment-backed
        credential pool -- so a provider whose key is missing or still a placeholder
        is never selected, and a route with no usable configuration is never called.
        """
        if provider in {"mock"}:
            return True, ""
        label = {
            "huggingface": "Hugging Face",
            "gemini": "Gemini",
            "groq": "Groq",
            "openrouter": "OpenRouter",
        }.get(provider)
        if label is not None:
            # Model readiness is checked before fetching credentials so that a
            # provider with a DB-only model can still be used, but a bare key never
            # turns on a guessed model id. A model id already proven eligible by the
            # registry (verified, free, enabled) is equally explicit, so a custom
            # registry must not be rejected just because GROQ_MODEL is unset.
            if provider == "groq" and not (
                settings.GROQ_MODEL
                or self._db_model_for("groq")
                or self._registry_model_verified("groq")
            ):
                return False, "Groq model id is not configured in GROQ_MODEL or the provider registry"
            try:
                configured_credentials = get_provider_credentials(label)
            except ValueError:
                return False, f"{label} adapter does not support credential configuration"
            if not configured_credentials:
                return False, f"{label} API key is not configured"
            # Having a key is not enough for model families that require an
            # operator-verified account/model id; do not route a key at a guessed
            # or empty identifier.
            if provider == "huggingface" and not (settings.HF_TEXT_MODEL or self._db_model_for("huggingface")):
                return False, "Hugging Face text model id is not configured"
            return True, ""
        if provider == "muse_spark":
            configured = bool(settings.MUSE_API_URL and settings.MUSE_API_KEY and settings.MUSE_SPARK_MODEL)
            return configured, "" if configured else "Muse Spark endpoint/key/model is not configured"
        if provider == "cloudflare":
            configured = bool(
                settings.CLOUDFLARE_ACCOUNT_ID
                and settings.CLOUDFLARE_API_TOKEN
                and settings.CLOUDFLARE_RESEARCH_MODEL
                and settings.CLOUDFLARE_RESEARCH_MODEL_VERIFIED
            )
            return configured, "" if configured else "Cloudflare research is not configured or verified"
        return False, f"Provider '{provider}' has no enabled adapter"

    def _registry_model_verified(self, provider: str) -> bool:
        """Does the active registry already carry a verified free model for `provider`?

        A registry entry is an explicit operator statement (verified, free, enabled),
        so it satisfies the "no guessed model id" rule the same way GROQ_MODEL does.
        Without this, a verified Groq entry was dropped from the fallback chain and a
        retryable primary failure became "All eligible providers failed".
        """
        registry = self.registry
        return any(
            spec.provider == provider
            and spec.enabled
            and spec.verified_free
            and spec.availability == "available"
            and (spec.model or "").strip()
            for spec in registry
        )

    def _db_model_for(self, provider: str) -> str:
        """Model id supplied by a database row for this provider (may be empty)."""
        return self._db_overrides_model.get(provider, "")

    def eligible_routes(self, capability: str = "chat", db: Session | None = None) -> tuple[Route, ...]:
        if self.mode == "dev":
            return (Route("mock", "mock-chat"),)
        registry = self.registry_for(db)
        # Models supplied by *enabled* database overrides (no environment default,
        # e.g. a Groq model an administrator verified) so provider() can build them.
        # Disabled rows are tombstones and must not satisfy the model readiness check.
        self._db_overrides_model = {
            spec.provider: spec.model
            for spec in registry
            if spec.source == "database" and spec.enabled and (spec.model or "").strip()
        }
        routes = []
        for spec in registry:
            if spec.provider == "mock":
                continue
            if not spec.verified_free or capability not in spec.capabilities:
                continue
            if spec.provider not in self.preferences():
                continue
            ready, _reason = self.provider_ready(spec.provider)
            if not ready:
                continue
            if not (spec.model or "").strip():
                # A provider whose model id was never configured cannot be called;
                # selecting it would only fail later, after quota was reserved.
                continue
            candidate = Route(spec.provider, spec.model, True)
            if candidate not in routes:
                routes.append(candidate)
        if (
            capability in {"chat", "research", "multimodal"}
            and settings.MUSE_SPARK_VERIFIED
            and settings.MUSE_API_URL
            and settings.MUSE_API_KEY
            and settings.MUSE_SPARK_MODEL
            and settings.MUSE_SPARK_REQUEST_COST_USD > 0
            and settings.MUSE_SPARK_MONTHLY_USD > 0
        ):
            routes.append(Route("muse_spark", settings.MUSE_SPARK_MODEL, False))
        if (
            capability == "research"
            and settings.CLOUDFLARE_RESEARCH_MODEL_VERIFIED
            and settings.CLOUDFLARE_ACCOUNT_ID
            and settings.CLOUDFLARE_API_TOKEN
            and settings.CLOUDFLARE_RESEARCH_MODEL
            and settings.CLOUDFLARE_RESEARCH_REQUEST_NEURONS > 0
            and settings.CLOUDFLARE_DAILY_NEURON_LIMIT > 0
        ):
            routes.append(Route("cloudflare", settings.CLOUDFLARE_RESEARCH_MODEL, False))
        # Stable sort: provider preference order first, registry order second.
        preference = self.preferences()
        routes.sort(key=lambda route: preference.index(route.provider) if route.provider in preference else len(preference))
        return tuple(routes)

    def preferences(self) -> tuple[str, ...]:
        """Admin-configured provider order, restricted to the free allow-list.

        The allow-list is read from settings on every call: caching it in
        ``__init__`` meant a later change (an admin toggle, or a test patching
        ``FREE_PROVIDERS``) was ignored and the fallback route was silently
        dropped, so a retryable primary failure ended as "All eligible providers
        failed" instead of trying the next provider.
        """
        allowed_names = settings.FREE_PROVIDERS or self.order or ()
        allowed = {name.lower().strip() for name in allowed_names if str(name).strip()}
        order = self.order or tuple(name.strip().lower() for name in allowed_names if str(name).strip())
        return tuple(name for name in order if name in allowed)

    def route(self, capability: str = "chat", db: Session | None = None) -> Route:
        routes = self.eligible_routes(capability, db=db)
        if routes:
            return routes[0]
        raise GatewayUnavailableError(self.unavailable_message(db))

    def unavailable_message(self, db: Session | None = None) -> str:
        """Explain why nothing is routable without revealing any credential.

        Returning one opaque string made a fresh deployment impossible to debug:
        "no eligible provider" looked identical whether the key was missing, the
        provider was outside the free allow-list, or the model was unverified.
        """
        registry = self.registry_for(db)
        reasons: list[str] = []
        for spec in registry:
            if spec.provider == "mock" or not spec.capabilities.intersection({"chat", "research", "multimodal"}):
                continue
            if spec.provider not in self.preferences():
                reasons.append(f"{spec.provider}: not in FREE_PROVIDERS")
                continue
            ready, reason = self.provider_ready(spec.provider)
            if not ready:
                reasons.append(f"{spec.provider}: {reason}")
            elif not spec.verified_free:
                reasons.append(f"{spec.provider}: model not verified as free/available")
        detail = "; ".join(dict.fromkeys(reasons)) or "no provider is configured"
        return (
            "No eligible free AI provider is available. Configure an API key "
            f"(OPENROUTER_KEY_1, GEMINI_KEY_1 or GROQ_API_KEY_1). Reasons: {detail}"
        )

    def apply_admin_order(self, db: Session) -> None:
        """Read the admin-configured provider order from the database (if any)."""
        try:
            from app.services.feature_flags import load_gateway_order

            order = load_gateway_order(db)
            if order:
                self.set_order(order)
        except Exception:  # noqa: BLE001 - ordering is an optimisation, never a hard failure
            pass

    def provider(self, route: Route):
        '''Construct a route adapter without crossing the credential boundary.

        Registry entries and environment values can change between route selection
        and execution (admin toggled a key, deploy config rotated). Re-check readiness
        here so every provider -- not just OpenRouter -- fails closed.
        '''
        if route.provider == "mock":
            return MockProvider()
        ready, reason = self.provider_ready(route.provider)
        if not ready:
            raise GatewayUnavailableError(f"{route.provider} is not configured: {reason}")
        if route.provider == "openrouter":
            credentials = get_provider_credentials("OpenRouter")
            if not credentials:
                raise GatewayUnavailableError("OpenRouter is disabled until an OPENROUTER_KEY_n is configured")
            return OpenRouterProvider(
                credentials, route.model,
                base_url=settings.OPENROUTER_BASE_URL,
                timeout=settings.AI_REQUEST_TIMEOUT_SECONDS,
            )
        if route.provider == "gemini":
            return GeminiGatewayProvider(route.model, timeout=settings.AI_REQUEST_TIMEOUT_SECONDS)
        if route.provider == "huggingface":
            return HuggingFaceGatewayProvider(
                settings.HF_TEXT_API_URL,
                settings.HF_TEXT_MODEL or route.model or self._db_model_for("huggingface"),
            )
        if route.provider == "groq":
            return GroqGatewayProvider(
                settings.GROQ_API_URL,
                settings.GROQ_MODEL or self._db_model_for("groq") or route.model,
                timeout=settings.AI_REQUEST_TIMEOUT_SECONDS,
            )
        if route.provider == "muse_spark":
            return MuseSparkGatewayProvider(settings.MUSE_API_URL, settings.MUSE_API_KEY, settings.MUSE_SPARK_MODEL, timeout=settings.AI_REQUEST_TIMEOUT_SECONDS)
        if route.provider == "cloudflare":
            return CloudflareGatewayProvider(settings.CLOUDFLARE_ACCOUNT_ID, settings.CLOUDFLARE_API_TOKEN, settings.CLOUDFLARE_RESEARCH_MODEL, timeout=settings.AI_REQUEST_TIMEOUT_SECONDS)
        raise GatewayUnavailableError(f"Provider adapter not enabled for {route.provider}")

    def generate(self, prompt: str, *, system_prompt: str | None = None, capability: str = "chat", conversation: list[dict] | None = None, preferred_provider: str | None = None) -> tuple[str, Route]:
        if conversation:
            prompt = _flatten_conversation(conversation)
        routes = self.eligible_routes(capability)
        if self.mode == "dev":
            routes = (Route("mock", "mock-chat"),)
        if not routes:
            raise GatewayUnavailableError("No configured eligible free AI provider is available")
        if preferred_provider:
            routes = tuple(sorted(routes, key=lambda route: 0 if route.provider == preferred_provider else 1))
        last_error: Exception | None = None
        for route in routes:
            try:
                result = self.provider(route).generate(prompt, system_prompt=system_prompt, model=route.model)
                text = result.text if hasattr(result, "text") else str(result)
                return text, route
            except GatewayProviderError as exc:
                last_error = exc
                # An uncertain timeout must not fall through and create a second
                # billable generation whose first response may still complete.
                if not exc.retryable or exc.uncertain:
                    raise
        raise GatewayUnavailableError("All eligible providers failed transiently") from last_error

    def generate_with_reservation(
        self,
        db: Session,
        *,
        reservation_id: str,
        prompt: str,
        system_prompt: str | None = None,
        capability: str = "chat",
        preferred_provider: str | None = None,
    ) -> tuple[str, Route]:
        """Generate through already-reserved quota, with route-by-route fallback.

        Unlike :meth:`generate`, this never reserves a second unit: the caller has
        already reserved, so every attempt is bound to that one reservation and the
        ledger is committed exactly once on success.
        """
        routes = self.eligible_routes(capability, db=db)
        if self.mode == "dev" and not routes:
            routes = self.eligible_routes(capability)
        if not routes:
            raise GatewayUnavailableError(
                "No provider is currently available. An administrator must enable a free provider."
            )
        if preferred_provider:
            routes = tuple(sorted(routes, key=lambda route: 0 if route.provider == preferred_provider else 1))
        quotas = QuotaService()
        last_error: Exception | None = None
        for route in routes:
            quotas.rebind(db, reservation_id, provider=route.provider, model=route.model)
            try:
                budget_reservation = None
                if route.provider == "muse_spark":
                    from app.services.provider_budget import reserve as reserve_budget
                    if settings.MUSE_SPARK_REQUEST_COST_USD <= 0:
                        raise GatewayUnavailableError("Muse Spark billing cost is not configured; route is disabled")
                    budget_reservation = reserve_budget(db, request_id=f"{reservation_id}:muse", provider="muse_spark", bucket="spark", period="monthly", units=settings.MUSE_SPARK_REQUEST_COST_USD)
                elif route.provider == "cloudflare":
                    from app.services.provider_budget import reserve as reserve_budget
                    if settings.CLOUDFLARE_RESEARCH_REQUEST_NEURONS <= 0:
                        raise GatewayUnavailableError("Cloudflare research neuron cost is not configured; route is disabled")
                    budget_reservation = reserve_budget(db, request_id=f"{reservation_id}:cloudflare", provider="cloudflare", bucket="research", period="daily", units=settings.CLOUDFLARE_RESEARCH_REQUEST_NEURONS)
                result = self.provider(route).generate(prompt, system_prompt=system_prompt, model=route.model)
                text = result.text if hasattr(result, "text") else str(result)
                if budget_reservation:
                    from app.services.provider_budget import commit as commit_budget
                    commit_budget(db, budget_reservation.id, detail={"route": route.provider, "model": route.model})
                quotas.commit(db, reservation_id, actual_units=1, detail={"capability": capability, "route": route.provider})
                return text, route
            except ProviderBudgetError as exc:
                # A paid/provider-specific cap must not consume the user's
                # generic quota or prevent a compatible fallback route.
                if budget_reservation:
                    from app.services.provider_budget import release as release_budget
                    release_budget(db, budget_reservation.id, detail={"failure": str(exc)[:160]})
                last_error = exc
                continue
            except GatewayUnavailableError as exc:
                if budget_reservation:
                    from app.services.provider_budget import release as release_budget
                    release_budget(db, budget_reservation.id, detail={"failure": str(exc)[:160]})
                last_error = exc
                continue
            except GatewayProviderError as exc:
                if budget_reservation:
                    from app.services.provider_budget import release as release_budget
                    release_budget(db, budget_reservation.id, detail={"failure": str(exc)[:160]})
                last_error = exc
                if exc.uncertain:
                    quotas.mark_uncertain(db, reservation_id)
                    raise
                if not exc.retryable:
                    quotas.release(db, reservation_id, failure=True)
                    raise
            except Exception as exc:
                if budget_reservation:
                    from app.services.provider_budget import release as release_budget
                    release_budget(db, budget_reservation.id, detail={"failure": str(exc)[:160]})
                quotas.release(db, reservation_id, failure=True)
                raise GatewayUnavailableError(str(exc)) from exc
        quotas.release(db, reservation_id, failure=True)
        raise GatewayUnavailableError("All eligible providers failed transiently") from last_error

    def generate_with_quota(
        self,
        db: Session,
        *,
        user_id: int,
        request_id: str,
        prompt: str,
        system_prompt: str | None = None,
        capability: str = "chat",
        chat_id: str | None = None,
        quota_service: QuotaService | None = None,
    ) -> tuple[str, Route]:
        routes = self.eligible_routes(capability, db=db)
        if not routes:
            raise GatewayUnavailableError(self.unavailable_message(db))
        quotas = quota_service or QuotaService()
        reservation = quotas.reserve(
            db,
            user_id=user_id,
            request_id=request_id,
            provider=routes[0].provider,
            model=routes[0].model,
            chat_id=chat_id,
            capability=capability,
        )
        last_error: Exception | None = None
        for index, route in enumerate(routes):
            if index:
                quotas.rebind(db, reservation.reservation_id, provider=route.provider, model=route.model)
            try:
                result = self.provider(route).generate(prompt, system_prompt=system_prompt, model=route.model)
                text = result.text if hasattr(result, "text") else str(result)
                quotas.commit(
                    db,
                    reservation.reservation_id,
                    actual_units=1,
                    detail={"capability": capability, "route": route.provider},
                )
                return text, route
            except GatewayProviderError as exc:
                last_error = exc
                if exc.uncertain:
                    quotas.mark_uncertain(db, reservation.reservation_id)
                    raise
                if not exc.retryable:
                    quotas.release(db, reservation.reservation_id, failure=True)
                    raise
            except Exception:
                # Any unexpected adapter error must not strand the already-reserved
                # unit. Provider adapters normally normalize errors, but an unrelated
                # integration failure can still escape their boundary.
                quotas.release(db, reservation.reservation_id, failure=True)
                raise
        quotas.release(db, reservation.reservation_id, failure=True)
        raise GatewayUnavailableError("All eligible providers failed transiently") from last_error
