"""Capability-specific managers behind the shared AI gateway policy.

Managers deliberately own their provider families; quota, admin selection and
fallback policy remain shared and are never implemented in the frontend.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings
from app.services.ai_gateway.gateway import AIGateway, GatewayUnavailableError, Route


@dataclass(frozen=True)
class CapabilityStatus:
    capability: str
    providers: tuple[str, ...]
    paid_enabled: bool


class ChatProviderManager:
    capability = "chat"

    def __init__(self, gateway: AIGateway | None = None) -> None:
        self.gateway = gateway or AIGateway()

    def routes(self, db=None) -> tuple[Route, ...]:
        return self.gateway.eligible_routes(self.capability, db=db)

    def generate(self, *args, **kwargs):
        return self.gateway.generate_with_quota(*args, capability=self.capability, **kwargs)


class ImageProviderManager:
    capability = "image"

    def status(self) -> CapabilityStatus:
        providers = ("cloudflare", "gemini") if settings.CLOUDFLARE_API_TOKEN and settings.CLOUDFLARE_ACCOUNT_ID else ("gemini",)
        return CapabilityStatus(self.capability, providers, paid_enabled=False)

    def require_configured(self) -> None:
        if not (settings.CLOUDFLARE_API_TOKEN and settings.CLOUDFLARE_ACCOUNT_ID):
            raise GatewayUnavailableError("Cloudflare image provider is not configured")


class VideoProviderManager:
    capability = "video"

    def status(self) -> CapabilityStatus:
        providers = []
        if settings.HF_TEXT_TO_VIDEO_SPACE or settings.HF_IMAGE_TO_VIDEO_SPACE:
            providers.append("huggingface-wan")
        if settings.LTX_API_KEY:
            providers.append("ltx")
        if settings.FAL_KEY and settings.PAID_PROVIDERS_ENABLED:
            providers.append("fal")
        return CapabilityStatus(self.capability, tuple(providers), settings.PAID_PROVIDERS_ENABLED)

    def require_provider(self, mode: str) -> None:
        status = self.status()
        if not status.providers:
            raise GatewayUnavailableError("No enabled video provider is configured")
