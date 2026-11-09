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
    MONGODB_URL: str = ""
    MONGODB_DATABASE: str = "enterprise_ai_db"
    AI_GATEWAY_MODE: str = "dev"
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
    CORS_ORIGINS: str = ""
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


try:
    settings = Settings()
except Exception as exc:  # noqa: BLE001 - re-raised with deploy guidance
    # The raw pydantic error ("String should have at least 32 characters") is the
    # first thing a failed deploy prints and says nothing about where to fix it.
    # Hosted platforms have no .env file, so the value has to be added to the
    # service environment instead.
    message = str(exc)
    if "JWT_SECRET" in message:
        raise RuntimeError(
            "JWT_SECRET is missing or too short (minimum 32 characters).\n"
            "Local:   set it in backend/.env (see backend/.env.example).\n"
            "Render:  Dashboard -> your service -> Environment -> add JWT_SECRET.\n"
            "Generate: python -c \"import secrets; print(secrets.token_urlsafe(48))\"\n"
            f"Original error: {message}"
        ) from exc
    raise
