import pytest
from uuid import uuid4
from datetime import datetime, timezone


def test_gateway_defaults_to_mock_without_network(monkeypatch):
    monkeypatch.setenv("AI_GATEWAY_MODE", "dev")
    # settings is a module-level object read at construction time, so the
    # env var alone is not enough once the settings instance exists.
    monkeypatch.setattr("app.services.ai_gateway.gateway.settings.AI_GATEWAY_MODE", "dev")
    from app.services.ai_gateway import AIGateway

    gateway = AIGateway()
    text, route = gateway.generate("hello")
    assert route.provider == "mock"
    assert route.model == "mock-chat"
    assert text == "[mock:mock-chat] hello"


def test_gateway_never_selects_openrouter_without_key(monkeypatch):
    monkeypatch.setenv("AI_GATEWAY_MODE", "prod")
    monkeypatch.setattr("app.services.ai_gateway.gateway.settings.AI_GATEWAY_MODE", "prod")
    monkeypatch.setattr("app.services.ai_gateway.gateway.settings.OPENROUTER_KEY_1", "")
    monkeypatch.setattr("app.services.ai_gateway.gateway.settings.OPENROUTER_KEY_2", "")
    from app.services.ai_gateway import AIGateway

    monkeypatch.setattr("app.services.ai_gateway.gateway.get_provider_credentials", lambda name: ())
    gateway = AIGateway()
    from app.services.ai_gateway import GatewayUnavailableError
    with pytest.raises(GatewayUnavailableError):
        gateway.route("chat")


def test_openai_legacy_provider_fails_closed():
    from app.services.ai_provider import OpenAIProvider

    provider = OpenAIProvider("test-key", "gpt-4")
    with pytest.raises(RuntimeError, match="disabled"):
        provider.generate_response("hello")


def test_mongo_projection_is_idempotent(db_session):
    from app.api.v1.endpoints.auth import mirror_to_sql

    first = mirror_to_sql(
        db_session,
        "mongo-user@example.com",
        "hash",
        "Mongo User",
        None,
        mongo_id="65abc",
        email="mongo-user@example.com",
    )
    second = mirror_to_sql(
        db_session,
        "mongo-user@example.com",
        "hash-2",
        "Updated Mongo User",
        None,
        mongo_id="65abc",
        email="mongo-user@example.com",
    )
    assert first.id == second.id
    assert second.mongo_id == "65abc"
    assert second.full_name == "Updated Mongo User"
    assert db_session.query(type(second)).filter_by(mongo_id="65abc").count() == 1


def _user(db):
    from app.models.user import User
    user = User(identifier=f"quota-{uuid4().hex}", email=f"quota-{uuid4().hex}@example.com", hashed_password="hash", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_quota_reserve_commit_is_idempotent(db_session):
    from app.models.gateway import UsageLog
    from app.services.ai_gateway.quota import QuotaService, DuplicateRequest

    user = _user(db_session)
    service = QuotaService()
    request_id = f"req-commit-{uuid4().hex}"
    reservation = service.reserve(db_session, user_id=user.id, request_id=request_id, provider="mock", model="mock-chat")
    usage = service.commit(db_session, reservation.reservation_id, actual_units=3)
    assert usage.units == 3
    assert service.commit(db_session, reservation.reservation_id, actual_units=3).id == usage.id
    with pytest.raises(DuplicateRequest):
        service.reserve(db_session, user_id=user.id, request_id=request_id, provider="mock", model="mock-chat")
    assert db_session.query(UsageLog).filter_by(request_id=request_id).count() == 1


def test_quota_release_and_uncertain_do_not_refund_uncertain(db_session):
    from app.models.gateway import QuotaReservation, UserQuota
    from app.services.ai_gateway.quota import QuotaService, ReservationState

    user = _user(db_session)
    service = QuotaService()
    released = service.reserve(db_session, user_id=user.id, request_id=f"req-release-{uuid4().hex}", provider="mock", model="mock-chat")
    service.release(db_session, released.reservation_id)
    assert released.state == ReservationState.RELEASED
    quota = db_session.query(UserQuota).filter_by(user_id=user.id).one()
    assert quota.reserved == 0

    uncertain = service.reserve(db_session, user_id=user.id, request_id=f"req-unknown-{uuid4().hex}", provider="mock", model="mock-chat")
    service.mark_uncertain(db_session, uncertain.reservation_id)
    stored = db_session.query(QuotaReservation).filter_by(reservation_id=uncertain.reservation_id).one()
    assert stored.state == ReservationState.UNKNOWN
    assert db_session.query(UserQuota).filter_by(user_id=user.id).one().reserved == 1


def test_daily_and_per_chat_limits_are_independent(db_session):
    from app.services.ai_gateway.quota import QuotaService, QuotaExhausted

    user = _user(db_session)
    service = QuotaService()
    service.set_limits(db_session, user_id=user.id, daily_limit=2, per_chat_limit=1)
    first = service.reserve(db_session, user_id=user.id, request_id=f"req-limit-{uuid4().hex}", provider="mock", model="mock-chat", chat_id="chat-a")
    service.commit(db_session, first.reservation_id)
    with pytest.raises(QuotaExhausted):
        service.reserve(db_session, user_id=user.id, request_id=f"req-limit-{uuid4().hex}", provider="mock", model="mock-chat", chat_id="chat-a")
    second = service.reserve(db_session, user_id=user.id, request_id=f"req-limit-{uuid4().hex}", provider="mock", model="mock-chat", chat_id="chat-b")
    assert second.chat_id == "chat-b"


def test_gemini_adapter_normalizes_mocked_success_and_rate_limit(monkeypatch):
    from app.services.ai_gateway import providers
    from backend.key_rotator import ProviderCredential, ProviderRateLimitError

    monkeypatch.setattr(providers, "call_with_key_rotation", lambda *args, **kwargs: type("Result", (), {"text": "ok"})())
    adapter = providers.GeminiGatewayProvider("gemini-test", (ProviderCredential("secret", "GEMINI_KEY_1"),))
    result = adapter.generate("hello")
    assert result.provider == "gemini"
    assert result.model == "gemini-test"
    assert result.text == "ok"

    def limited(*args, **kwargs):
        raise ProviderRateLimitError("Gemini", "GEMINI_KEY_1", 30, 429, "safe")
    monkeypatch.setattr(providers, "call_with_key_rotation", limited)
    with pytest.raises(providers.GatewayProviderError) as raised:
        adapter.generate("hello")
    assert raised.value.retryable is True
    assert raised.value.status_code == 429


def test_huggingface_adapter_uses_mocked_rotation(monkeypatch):
    from app.services.ai_gateway import providers
    from backend.key_rotator import ProviderCredential

    monkeypatch.setattr(providers, "call_with_key_rotation", lambda *args, **kwargs: "hf answer")
    adapter = providers.HuggingFaceGatewayProvider(
        "https://example.invalid/chat", "verified-model", (ProviderCredential("secret", "HF_TOKEN_1"),)
    )
    result = adapter.generate("hello")
    assert result.provider == "huggingface"
    assert result.text == "hf answer"


def test_unknown_or_paid_registry_entries_are_not_eligible(monkeypatch):
    from app.core.config import settings
    from app.services.ai_gateway import AIGateway, GatewayUnavailableError
    from app.services.ai_gateway.registry import ModelSpec

    monkeypatch.setattr(settings, "AI_GATEWAY_MODE", "prod")
    registry = (
        ModelSpec("gemini", "paid-model", True, False, frozenset({"chat"}), availability="available"),
        ModelSpec("huggingface", "unknown-model", True, None, frozenset({"chat"}), availability="available"),
    )
    with pytest.raises(GatewayUnavailableError):
        AIGateway(registry=registry).route("chat")


def test_gateway_quota_path_falls_back_and_commits_one_usage(db_session, monkeypatch):
    from app.core.config import settings
    from app.models.gateway import UsageLog
    from app.services.ai_gateway import AIGateway
    from app.services.ai_gateway.providers import GatewayProviderError, GatewayResponse
    from app.services.ai_gateway.registry import ModelSpec

    user = _user(db_session)
    monkeypatch.setattr(settings, "AI_GATEWAY_MODE", "prod")
    monkeypatch.setattr(settings, "OPENROUTER_KEY_1", "configured-for-test")
    verified = lambda model: ModelSpec(
        "openrouter", model, True, True, frozenset({"chat"}),
        availability="available", verified_at=datetime.now(timezone.utc),
    )
    gateway = AIGateway(registry=(verified("first"), verified("second")))
    calls = []

    def fake_provider(route):
        class Provider:
            def generate(self, prompt, *, system_prompt=None, model=None):
                calls.append(model)
                if model == "first":
                    raise GatewayProviderError("busy", status_code=503, retryable=True)
                return GatewayResponse("fallback answer", "openrouter", model)
        return Provider()

    monkeypatch.setattr(gateway, "provider", fake_provider)
    request_id = f"fallback-{uuid4().hex}"
    answer, route = gateway.generate_with_quota(
        db_session, user_id=user.id, request_id=request_id, prompt="hello"
    )
    assert answer == "fallback answer"
    assert route.model == "second"
    assert calls == ["first", "second"]
    assert db_session.query(UsageLog).filter_by(request_id=request_id).count() == 1


def test_gateway_quota_path_marks_timeout_uncertain(db_session, monkeypatch):
    from app.core.config import settings
    from app.models.gateway import QuotaReservation
    from app.services.ai_gateway import AIGateway
    from app.services.ai_gateway.providers import GatewayProviderError
    from app.services.ai_gateway.registry import ModelSpec

    user = _user(db_session)
    monkeypatch.setattr(settings, "AI_GATEWAY_MODE", "prod")
    monkeypatch.setattr(settings, "OPENROUTER_KEY_1", "configured-for-test")
    spec = ModelSpec("openrouter", "timeout-model", True, True, frozenset({"chat"}), availability="available", verified_at=datetime.now(timezone.utc))
    gateway = AIGateway(registry=(spec,))

    class Provider:
        def generate(self, prompt, *, system_prompt=None, model=None):
            raise GatewayProviderError("timeout", uncertain=True)

    monkeypatch.setattr(gateway, "provider", lambda route: Provider())
    request_id = f"timeout-{uuid4().hex}"
    with pytest.raises(GatewayProviderError):
        gateway.generate_with_quota(db_session, user_id=user.id, request_id=request_id, prompt="hello")
    reservation = db_session.query(QuotaReservation).filter_by(request_id=request_id).one()
    assert reservation.state == "UNKNOWN"
    assert reservation.uncertain is True
