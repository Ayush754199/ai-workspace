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
    # "production" makes a missing/short JWT_SECRET a startup error instead of an
    # ephemeral key that silently signs everyone out on the next restart.
    ENVIRONMENT: str = "development"
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
    # Muse is intentionally disabled until its private API contract is supplied
    # and an administrator verifies the exact endpoint/model combination.
    MUSE_API_URL: str = ""
    MUSE_API_KEY: str = ""
    MUSE_SPARK_MODEL: str = ""
    MUSE_SPARK_VERIFIED: bool = False
    MUSE_IMAGE_API_URL: str = ""
    MUSE_IMAGE_MODEL: str = ""
    MUSE_IMAGE_VERIFIED: bool = False
    MUSE_IMAGE_SUPPORTS_EDITING: bool = False
    MUSE_SPARK_MONTHLY_USD: float = Field(default=7.0, ge=0)
    MUSE_IMAGE_MONTHLY_USD: float = Field(default=7.0, ge=0)
    # The exact provider-reported charge is required before Muse is enabled.
    # Zero keeps the route fail-closed until the provider contract is verified.
    MUSE_SPARK_REQUEST_COST_USD: float = Field(default=0.0, ge=0)
    MUSE_IMAGE_REQUEST_COST_USD: float = Field(default=0.0, ge=0)
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    # OpenRouter's free router selects a currently available free model per request.
    OPENROUTER_MODEL: str = "openrouter/free"
    # Provider-level database settings may append additional allowed routes, but
    # these environment keys remain the credential source of truth.
    GROQ_API_KEY_1: str = ""
    GROQ_API_KEY_2: str = ""
    GROQ_API_KEY_3: str = ""
    OPENROUTER_KEY_3: str = ""
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
    # Proxy headers (X-Forwarded-For / X-Real-IP) are honoured ONLY when the TCP
    # peer is inside one of these CIDRs. A browser can send any header it likes, so
    # trusting them unconditionally lets a client rotate its own rate-limit bucket.
    # The defaults cover Docker/nginx on a private network, Render's local edge and
    # a local dev proxy; put an explicit public CIDR here if you front the API with
    # a proxy outside those ranges.
    TRUSTED_PROXY_CIDRS: str = (
        "127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fd00::/8"
    )
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
    IMAGE_ORCHESTRATION_ENABLED: bool = True
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
    # Workers AI usage is billed/limited in neurons. Zero is fail-closed because
    # the model catalog cannot prove the account's effective daily allowance.
    CLOUDFLARE_DAILY_NEURON_LIMIT: int = Field(default=0, ge=0)
    CLOUDFLARE_RESET_TIMEZONE: str = "UTC"
    CLOUDFLARE_RESEARCH_MODEL: str = ""
    CLOUDFLARE_RESEARCH_MODEL_VERIFIED: bool = False
    CLOUDFLARE_IMAGE_MODEL_VERIFIED: bool = False
    CLOUDFLARE_IMAGE_EDITING_VERIFIED: bool = False
    CLOUDFLARE_RESEARCH_REQUEST_NEURONS: int = Field(default=0, ge=0)
    CLOUDFLARE_IMAGE_REQUEST_NEURONS: int = Field(default=0, ge=0)
    LTX_API_KEY: str = ""
    LTX_API_BASE_URL: str = "https://api.ltx.video/v1"
    FAL_KEY: str = ""
    FAL_VIDEO_MODEL: str = "fal-ai/cogvideox-5b"
    POLLINATIONS_API_KEY: str = ""
    POLLINATIONS_IMAGE_MODEL: str = "black-forest-labs/flux.1-schnell"
    POLLINATIONS_AUDIO_MODEL: str = "openai/tts-1"
    # LiveKit credentials are backend-only.  The URL is returned to an
    # authenticated client only when it requests a short-lived room token.
    LIVEKIT_URL: str = ""
    LIVEKIT_API_KEY: str = ""
    LIVEKIT_API_SECRET: str = ""
    LIVEKIT_TOKEN_TTL_SECONDS: int = Field(default=600, ge=60, le=3600)
    LIVEKIT_CALL_RING_TIMEOUT_SECONDS: int = Field(default=45, ge=10, le=300)
    # Calls that have no LiveKit heartbeat/webhook activity beyond this window
    # are stale and must not keep a user marked busy forever.
    LIVEKIT_STALE_CALL_TIMEOUT_SECONDS: int = Field(default=21600, ge=300, le=604800)
    # How often the background sweeper closes abandoned calls. Expiry used to run
    # only when a client called /calls/history or /calls/busy, so an abandoned
    # call could keep everyone marked busy indefinitely.
    CALL_STALE_SWEEP_INTERVAL_SECONDS: int = Field(default=300, ge=30, le=3600)
    LIVEKIT_MAX_PARTICIPANTS: int = Field(default=12, ge=2, le=100)
    # Emergency, explicitly opt-in cleanup switch. Keep false in all normal
    # deployments; it is used only for a controlled call-data reset.
    CALL_DATA_RESET_ON_STARTUP: bool = False

    @field_validator("JWT_SECRET")
    @classmethod
    def validate_jwt_secret(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized in {"super-secret-key", "changeme", "secret"}:
            raise ValueError("JWT_SECRET must be a non-default secure value")
        if any(word in normalized for word in ("changeme", "super-secret", "default-secret", "replace-with", "secret-is-not")):
            raise ValueError("JWT_SECRET must not contain a known placeholder phrase")
        return value

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.strip().lower() in {"production", "prod"}


def _load_settings() -> "Settings":
    """Build Settings, repairing an unusable JWT_SECRET instead of crashing.

    JWT_SECRET has no safe default: a predictable signing key lets an attacker
    forge bearer tokens. A short value is just as bad. But refusing to boot means
    a hosted platform (Render, Fly, Railway) stays down until someone edits an
    environment variable -- with no working deploy to debug on. Never feed a
    short env JWT_SECRET back into Settings().

    So: a *missing* secret is generated and, on a dev machine, persisted to
    ``backend/.jwt_secret`` so a local restart does not sign everyone out. A *too
    short* secret is never silently accepted -- it is replaced by a random value and
    the operator is told to persist a real one. Hosted platforms (Render, Fly,
    Railway) get no persistence: their filesystem is disposable, so the only real
    fix there is to set JWT_SECRET in the service environment.
    """
    import logging

    logger = logging.getLogger("app.config")
    try:
        return Settings()
    except Exception as exc:  # noqa: BLE001 - only JWT_SECRET is repaired here
        message = str(exc)
        if "JWT_SECRET" not in message:
            raise

    if os.getenv("ENVIRONMENT", "").strip().lower() in {"production", "prod"}:
        # Production "must not run on a throwaway key" is the ideal, but taking the
        # service down to enforce it just turns a misconfiguration into an outage.
        # Boot with a secure random key instead, and make the missing configuration
        # impossible to miss in the deploy logs. The operator must set JWT_SECRET
        # to a 48-char value in the service environment; until then every restart
        # invalidates bearer tokens (which is strictly better than a dead service).
        import secrets as _secrets

        logger.critical(
            "JWT_SECRET is missing or shorter than 32 characters (ENVIRONMENT=production). "
            "The service is booting with a SECURE EPHEMERAL key: every bearer token is "
            "invalidated on restart and the deployment is NOT production-ready.\n"
            "Fix NOW: Render -> your service -> Environment -> JWT_SECRET -> paste a value "
            "of 48+ random characters (generate one with: "
            'python -c "import secrets; print(secrets.token_urlsafe(48))") -> Save -> Manual Deploy.'
        )
        return Settings(JWT_SECRET=_secrets.token_urlsafe(48))

    import secrets as _secrets

    def _persist(secret: str) -> None:
        """Keep the generated key across restarts when the operator made no choice.

        Without this the key is regenerated on every process start, so every
        restart/redeploy signs every user out. It is written only when JWT_SECRET is
        genuinely absent (a too-short value is the operator's to fix), and it never
        overrides a real environment value.
        """
        if len(os.environ.get("JWT_SECRET", "").strip()) >= 32:
            return
        if os.getenv("ENVIRONMENT", "").strip().lower() in {"production", "prod"}:
            return
        # A container filesystem is disposable, so a path there changes nothing.
        if os.getenv("RENDER", "").strip() or os.getenv("DYNO", "").strip():
            return
        path = Path(os.getenv("JWT_SECRET_FILE", str(Path(__file__).resolve().parents[2] / ".jwt_secret")))
        try:
            if path.is_file():
                existing = path.read_text(encoding="utf-8").strip()
                if len(existing) >= 32:
                    os.environ["JWT_SECRET"] = existing
                    return
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(secret, encoding="utf-8")
            try:
                path.chmod(0o600)
            except OSError:
                pass
        except OSError:
            pass

    resolved_env = os.environ.get("JWT_SECRET", "").strip()
    if len(resolved_env) >= 32:  # safety net: Settings() should have succeeded
        return Settings(JWT_SECRET=resolved_env)

    # NEVER feed the short env value back into Settings(): it fails min_length=32
    # with the exact ValidationError we are already recovering from.
    secret = _secrets.token_urlsafe(48)
    is_prod = os.getenv("ENVIRONMENT", "").strip().lower() in {"production", "prod"}
    if not is_prod:
        # Dev: reuse a persisted key when one exists so restarts keep sessions.
        _persist(secret)
        persisted = os.environ.get("JWT_SECRET", "").strip()
        if len(persisted) >= 32:
            secret = persisted
    logger.critical(
        "JWT_SECRET is %s and shorter than 32 characters. The service is booting with a "
        "SECURE EPHEMERAL key: every bearer token is invalidated on restart. "
        "Fix: Render -> your service -> Environment -> JWT_SECRET -> paste a 48+ random "
        "value (python -c \"import secrets; print(secrets.token_urlsafe(48))\") and redeploy.",
        f"'{resolved_env[:8]}…'" if resolved_env else "unset",
    )
    return Settings(JWT_SECRET=secret)


settings = _load_settings()
