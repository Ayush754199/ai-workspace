"""Request-level protections shared by every endpoint.

* Per-IP rate limiting for auth (brute force) and a global per-IP backstop.
* Conservative security headers on every response.
* Request ids so logs and client errors can be correlated.

The limiter is in-process and intentionally small: it protects a single
instance. For a multi-instance deployment put the same limits at the reverse
proxy (see docs/DEPLOYMENT.md).
"""

from __future__ import annotations

import hashlib
import threading
import time
import uuid
from collections import defaultdict, deque

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.config import settings


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: int = 60) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        with self._lock:
            bucket = self._hits[key]
            while bucket and now - bucket[0] > self.window:
                bucket.popleft()
            if len(bucket) >= self.limit:
                retry_after = max(1, int(self.window - (now - bucket[0])))
                return False, retry_after
            bucket.append(now)
            if len(self._hits) > 20_000:
                for stale in [k for k, v in self._hits.items() if not v][:1000]:
                    self._hits.pop(stale, None)
            return True, 0


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


auth_limiter = SlidingWindowLimiter(settings.AUTH_RATE_LIMIT_PER_MINUTE)
global_limiter = SlidingWindowLimiter(max(120, settings.AI_RATE_LIMIT_PER_MINUTE * 10))
ai_limiter = SlidingWindowLimiter(settings.AI_RATE_LIMIT_PER_MINUTE)

AI_PREFIXES = ("/chat", "/images", "/documents", "/office", "/voice", "/research", "/video", "/ai")


def _is_ai_path(path: str) -> bool:
    """Match both legacy routes and the versioned API routes."""
    normalized = path.removeprefix("/api/v1") or "/"
    return normalized.startswith(AI_PREFIXES)


def _ai_rate_limit_key(request: Request) -> str:
    """Use the authenticated bearer token as a non-secret per-user key."""
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        token_digest = hashlib.sha256(authorization[7:].encode("utf-8")).hexdigest()
        return f"user:{token_digest}"
    return f"ip:{client_ip(request)}"


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        path = request.url.path

        if request.method != "OPTIONS":
            if path.startswith(("/auth/login", "/auth/register", "/auth/signup")):
                allowed, retry_after = auth_limiter.allow(client_ip(request))
                if not allowed:
                    return JSONResponse(
                        {"detail": "Too many attempts. Please wait and try again."},
                        status_code=429,
                        headers={"Retry-After": str(retry_after), "X-Request-Id": request_id},
                    )
            if _is_ai_path(path):
                allowed, retry_after = ai_limiter.allow(_ai_rate_limit_key(request))
                if not allowed:
                    return JSONResponse(
                        {"detail": "AI request rate limit reached. Please wait and try again."},
                        status_code=429,
                        headers={"Retry-After": str(retry_after), "X-Request-Id": request_id},
                    )
                allowed, retry_after = global_limiter.allow(f"ai:{client_ip(request)}")
                if not allowed:
                    return JSONResponse(
                        {"detail": "Request rate limit reached. Slow down and retry."},
                        status_code=429,
                        headers={"Retry-After": str(retry_after), "X-Request-Id": request_id},
                    )

        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "microphone=(self), camera=()"
        if path.startswith(("/auth", "/chat", "/images", "/documents", "/office", "/voice", "/research", "/admin", "/api/v1")):
            response.headers["Cache-Control"] = "no-store"
        return response


def reset_limiters() -> None:
    """Test hook: drop all counters."""
    for limiter in (auth_limiter, global_limiter, ai_limiter):
        with limiter._lock:
            limiter._hits.clear()


def configure_limits() -> None:
    """Rebuild limiters from the current settings (useful for tests and reloads)."""
    global auth_limiter, global_limiter, ai_limiter
    auth_limiter = SlidingWindowLimiter(settings.AUTH_RATE_LIMIT_PER_MINUTE)
    global_limiter = SlidingWindowLimiter(max(120, settings.AI_RATE_LIMIT_PER_MINUTE * 10))
    ai_limiter = SlidingWindowLimiter(settings.AI_RATE_LIMIT_PER_MINUTE)
