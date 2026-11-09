def test_ai_rpm_limit_applies_to_versioned_routes_and_recovers(monkeypatch):
    from app.core import rate_limit

    clock = [1000.0]
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: clock[0])
    limiter = rate_limit.SlidingWindowLimiter(limit=2, window_seconds=60)
    assert limiter.allow("user:test")[0] is True
    assert limiter.allow("user:test")[0] is True
    allowed, retry_after = limiter.allow("user:test")
    assert allowed is False
    assert retry_after == 60

    clock[0] = 1061.0
    assert limiter.allow("user:test")[0] is True


def test_ai_path_normalizes_api_v1_prefix():
    from app.core.rate_limit import _is_ai_path

    assert _is_ai_path("/api/v1/chat/v2/conversations")
    assert _is_ai_path("/images/generate")
    assert not _is_ai_path("/api/v1/auth/me")
