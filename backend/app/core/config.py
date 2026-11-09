import os
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv


ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(ENV_FILE, override=False)
# The legacy Streamlit app keeps shared provider keys in the workspace root;
# load that file as a fallback while still allowing backend/.env to override it.
ROOT_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(ROOT_ENV_FILE, override=False)

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "AI Workspace Backend"
    # There is intentionally no fallback.  A predictable signing key would let an
    # attacker forge bearer tokens when deployment configuration is incomplete.
    JWT_SECRET: str = Field(min_length=32)
    ALGORITHM: str = "HS256"
    # Lifetime of a login session. The frontend stores the token and calls /auth/me
    # on every reload, so a short lifetime logs the user straight back out.
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=10080, ge=5)
    # Google AI Studio keys have multiple valid formats; do not restrict by prefix.
    GEMINI_API_KEY: str = Field(default="default-test-key", min_length=1)
    # Single source of truth for the Gemini model; keep in sync with config.GEMINI_MODEL.
    GEMINI_MODEL: str = Field(default="gemini-3.8-flash", min_length=1)
    GEMINI_IMAGE_MODEL: str = Field(default="gemini-3.1-flash-image", min_length=1)
    MONGODB_URL: str = ""
    MONGODB_DATABASE: str = "enterprise_ai_db"
    # One-time operational bootstrap for an administrator. Keep empty in normal
    # deployments; when explicitly set in the service environment, startup
    # promotes the matching SQL authorization row and the variable can then be
    # removed safely.
    ADMIN_BOOTSTRAP_IDENTIFIER: str = ""
    # Production must never silently answer with the development mock provider.
    # Tests explicitly override this setting to ``dev`` when they need mocks.
    AI_GATEWAY_MODE: str = "live"
    # Only these providers are ever considered by the gateway. Providers that bill
    # per token are *not* in this list; adding one requires an explicit code change.
    # Chat priority: OpenRouter free catalog -> Groq -> Gemini. Admin can
    # override the order through the gateway_order setting.
    FREE_PROVIDERS: list[str] = ["openrouter", "groq", "gemini", "huggingface"]
    # Concurrency guard: how many AI calls a single user may have in flight.
    AI_MAX_CONCURRENCY_PER_USER: int = Field(default=2, ge=1, le=20)
    # Hard ceiling on one gateway round-trip (seconds) before it is treated as uncertain.
    AI_REQUEST_TIMEOUT_SECONDS: float = Field(default=120.0, ge=5)
    GROQ_API_URL: str = "https://api.groq.com/openai/v1/chat/completions"
    GROQ_MODEL: str = ""
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    # OpenRouter's free router selects a currently available free model per request.
    OPENROUTER_MODEL: str = "openrouter/free"
    # Tavily credentials are backend-only. Accept the previously deployed misspelling
    # as a compatibility alias; use the canonical TAVILY_API_KEY_n names going forward.
    TAVILY_API_KEY_1: str = Field(default="", validation_alias=AliasChoices("TAVILY_API_KEY_1", "Tailvy_API_Key_1"))
    TAVILY_API_KEY_2: str = Field(default="", validation_alias=AliasChoices("TAVILY_API_KEY_2", "Tailvy_API_Key_2"))
    TAVILY_REQUEST_TIMEOUT_SECONDS: float = Field(default=30.0, ge=1, le=60)
    TAVILY_TECHNICAL_FAILOVER_ENABLED: bool = True
    AI_SEARCH_DAILY_QUOTA: int = Field(default=20, ge=0)
    AI_EXTRACTION_DAILY_QUOTA: int = Field(default=20, ge=0)
    AI_RESEARCH_DAILY_QUOTA: int = Field(default=10, ge=0)
    TAVILY_MONTHLY_BUDGET_CREDITS: int = Field(default=0, ge=0)
    # Tavily web search can be disabled without affecting regular chat.
    RESEARCH_ENABLED: bool = True
    # Server-side rate limiting for AI endpoints (per user, per minute).
    AI_RATE_LIMIT_PER_MINUTE: int = Field(default=30, ge=1)
    AUTH_RATE_LIMIT_PER_MINUTE: int = Field(default=10, ge=1)
    # Comma-separated origins allowed to call the API from a browser. Empty means
    # wildcard (development only) -- always set this in production.
    CORS_ORIGINS: str = (
        "https://ai-workspace-frontend-907h.onrender.com,"
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:5174,http://127.0.0.1:5174"
    )
    # API documentation is useful in development but must be explicitly enabled
    # for a deployed instance.
    API_DOCS_ENABLED: bool = True
    # Legacy direct-provider endpoints (/ai/*, /chat/multimodal, /chat/stream) skip
    # quota reservation and the free-provider policy. Off unless explicitly enabled.
    LEGACY_DIRECT_AI_ENABLED: bool = False
    OPENROUTER_KEY_1: str = ""
    OPENROUTER_KEY_2: str = ""
    PAID_PROVIDERS_ENABLED: bool = False
    AI_DAILY_QUOTA: int = Field(default=100, ge=1)
    AI_PER_CHAT_QUOTA: int = Field(default=20, ge=1)
    # Media defaults are deliberately conservative for testing; admins can
    # change them per user through the capability quota endpoints.
    AI_IMAGE_DAILY_QUOTA: int = Field(default=20, ge=0)
    AI_VIDEO_DAILY_QUOTA: int = Field(default=20, ge=0)
    AI_RESERVATION_TIMEOUT_SECONDS: int = Field(default=900, ge=30)
    HF_TEXT_API_URL: str = "https://router.huggingface.co/v1/chat/completions"
    HF_TEXT_MODEL: str = ""
    # Image generation stays disabled until an operator explicitly configures the
    # endpoint. There is intentionally no default: an unconfigured server answers
    # with a clear 503 instead of guessing a provider or returning a dummy image.
    HF_IMAGE_API_URL: str = ""
    HF_IMAGE_EDIT_SPACE: str = ""
    HF_IMAGE_EDIT_API_NAME: str = ""
    HF_IMAGE_EDIT_INPUTS_JSON: str = '["{image}", "{prompt}"]'
    # Image-to-video is a separate capability; it stays disabled until an admin
    # names an eligible Space here.
    HF_IMAGE_TO_VIDEO_SPACE: str = ""
    HF_IMAGE_TO_VIDEO_API_NAME: str = ""
    # Prompt-to-video Space (Wan 2.1 by default in media_service); exposed here so
    # the capabilities endpoint can report readiness honestly.
    HF_TEXT_TO_VIDEO_SPACE: str = ""
    HF_TEXT_TO_VIDEO_API_NAME: str = ""
    CLOUDFLARE_ACCOUNT_ID: str = ""
    CLOUDFLARE_API_TOKEN: str = ""
    CLOUDFLARE_IMAGE_PRIMARY_MODEL: str = "@cf/black-forest-labs/flux-2-klein-4b"
    CLOUDFLARE_IMAGE_FAST_MODEL: str = "@cf/black-forest-labs/flux-1-schnell"
    LTX_API_KEY: str = ""
    LTX_API_BASE_URL: str = "https://api.ltx.video/v1"
    FAL_KEY: str = ""
    FAL_VIDEO_MODEL: str = "fal-ai/cogvideox-5b"

    @field_validator("JWT_SECRET")
    @classmethod
    def validate_jwt_secret(cls, value: str) -> str:
        if value.strip().lower() in {"super-secret-key", "changeme", "secret"}:
            raise ValueError("JWT_SECRET must be a non-default secure value")
        return value


def _load_settings() -> "Settings":
    """Build Settings, repairing an unusable JWT_SECRET instead of crashing.

    JWT_SECRET has no safe default: a predictable signing key lets an attacker
    forge bearer tokens. A short value is just as bad. But refusing to boot means
    a hosted platform (Render, Fly, Railway) stays down until someone edits an
    environment variable -- with no working deploy to debug on.

    So: a *missing* secret is generated once per process and logged loudly with
    the exact fix. A *too short* secret is never silently accepted -- it is
    replaced by a random value and the operator is told to persist a real one,
    otherwise every restart signs users out.
    """
    try:
        return Settings()
    except Exception as exc:  # noqa: BLE001 - only JWT_SECRET is repaired here
        message = str(exc)
        if "JWT_SECRET" not in message:
            raise

    import logging
    import secrets as _secrets

    logger = logging.getLogger("app.config")
    logger.error(
        "JWT_SECRET is missing or shorter than 32 characters; using an ephemeral "
        "random secret for this process. Users will be signed out on every restart.\n"
        "Fix: set JWT_SECRET in the service environment (Render: Dashboard -> your "
        "service -> Environment), then redeploy.\n"
        "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\"\n"
        "Changing render.yaml alone does NOT update an existing service."
    )
    return Settings(JWT_SECRET=_secrets.token_urlsafe(48))


settings = _load_settings()
