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
from .registry import MODEL_REGISTRY, ModelSpec
from .quota import QuotaService


class GatewayPolicyError(ValueError):
    pass


class GatewayUnavailableError(RuntimeError):
    pass


def _flatten_conversation(conversation: list[dict]) -> str:
    """Render stored turns into one prompt for providers without a message API."""
    lines: list[str] = []
    for turn in conversation[-24:]:
        role = str(turn.get("role", "user")).capitalize()
        content = str(turn.get("content", "")).strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n\n".join(lines)


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
        # Admin-configurable preference order; settings values are the fallback.
        self.order: tuple[str, ...] = tuple(name.strip().lower() for name in settings.FREE_PROVIDERS if name.strip())

    def set_order(self, order: list[str] | tuple[str, ...] | None) -> None:
        if order:
            self.order = tuple(str(name).strip().lower() for name in order if str(name).strip())

    def eligible_routes(self, capability: str = "chat", db: Session | None = None) -> tuple[Route, ...]:
        if self.mode == "dev":
            return (Route("mock", "mock-chat"),)
        registry = self.registry
        if db is not None:
            from app.models.gateway import Provider, ProviderModel
            rows = (
                db.query(ProviderModel, Provider)
                .join(Provider, Provider.id == ProviderModel.provider_id)
                .filter(Provider.enabled.is_(True), ProviderModel.enabled.is_(True))
                .all()
            )
            if rows:
                registry = tuple(
                    ModelSpec(
                        "huggingface" if provider.name.casefold().replace(" ", "") == "huggingface" else provider.name.casefold(),
                        model.model,
                        model.enabled,
                        model.is_free,
                        frozenset(model.capabilities or []),
                        max_input_tokens=model.max_input_tokens,
                        max_output_tokens=model.max_output_tokens,
                        availability=model.availability,
                        verified_at=model.verified_at,
                        restrictions=tuple(model.restrictions or []),
                    )
                    for model, provider in rows
                )
        routes = []
        for spec in registry:
            if spec.provider == "mock":
                continue
            if not spec.verified_free or capability not in spec.capabilities:
                continue
            if spec.provider not in self.preferences():
                continue
            if spec.provider == "openrouter":
                if not get_provider_credentials("OpenRouter"):
                    continue
            if spec.provider == "groq" and not get_provider_credentials("Groq"):
                continue
            if spec.provider in {"gemini", "huggingface"} and not get_provider_credentials(
                "Gemini" if spec.provider == "gemini" else "Hugging Face"
            ):
                continue
            routes.append(Route(spec.provider, spec.model, True))
        if capability in {"chat", "research", "multimodal"} and settings.MUSE_SPARK_VERIFIED and settings.MUSE_API_URL and settings.MUSE_API_KEY and settings.MUSE_SPARK_MODEL:
            routes.append(Route("muse_spark", settings.MUSE_SPARK_MODEL, False))
        if capability == "research" and settings.CLOUDFLARE_RESEARCH_MODEL_VERIFIED and settings.CLOUDFLARE_ACCOUNT_ID and settings.CLOUDFLARE_API_TOKEN and settings.CLOUDFLARE_RESEARCH_MODEL:
            routes.append(Route("cloudflare", settings.CLOUDFLARE_RESEARCH_MODEL, False))
        # Stable sort: provider preference order first, registry order second.
        preference = self.preferences()
        routes.sort(key=lambda route: preference.index(route.provider) if route.provider in preference else len(preference))
        return tuple(routes)

    def preferences(self) -> tuple[str, ...]:
        """Admin-configured provider order, restricted to the free allow-list."""
        allowed = {name.lower().strip() for name in settings.FREE_PROVIDERS}
        return tuple(name for name in self.order if name in allowed)

    def route(self, capability: str = "chat", db: Session | None = None) -> Route:
        routes = self.eligible_routes(capability, db=db)
        if routes:
            return routes[0]
        raise GatewayUnavailableError("No configured eligible free AI provider is available")

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
        if route.provider == "mock":
            return MockProvider()
        if route.provider == "openrouter":
            credentials = get_provider_credentials("OpenRouter")
            if not credentials:
                raise GatewayUnavailableError("OpenRouter is disabled until OPENROUTER_KEY_1 or OPENROUTER_KEY_2 is configured")
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
                settings.HF_TEXT_MODEL or route.model,
            )
        if route.provider == "groq":
            return GroqGatewayProvider(
                settings.GROQ_API_URL,
                settings.GROQ_MODEL or route.model,
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
        for index, route in enumerate(routes):
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
            raise GatewayUnavailableError("No configured eligible free AI provider is available")
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
        quotas.release(db, reservation.reservation_id, failure=True)
        raise GatewayUnavailableError("All eligible providers failed transiently") from last_error
