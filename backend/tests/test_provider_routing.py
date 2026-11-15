"""Phase 1C: provider routing works on a fresh database and fails honestly.

Scenarios from the Phase 1 brief, all without network access and without printing
any credential:

1. fresh empty database + a configured key            -> route available
2. database with configured providers                 -> database row wins
3. missing API key                                    -> provider skipped
4. invalid/placeholder key                            -> provider skipped
5. primary provider unavailable, secondary available  -> one fallback, one commit
6. no provider at all                                 -> explicit error, no billing
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.core import config as config_module
from app.core.config import settings
from app.models.gateway import Provider, ProviderModel, UsageLog
from app.models.user import User
from app.services.ai_gateway import gateway as gateway_module
from app.services.ai_gateway import registry as registry_module
from app.services.ai_gateway.gateway import AIGateway
from app.services.ai_gateway.providers import GatewayProviderError, GatewayResponse
from app.services.ai_gateway.quota import QuotaService


@pytest.fixture()
def no_network(monkeypatch):
    """Fail the test if any provider adapter would be built for an unlisted key."""

    def credentials(name: str):
        return _FAKE_CREDENTIALS.get(name, ())

    _FAKE_CREDENTIALS.clear()
    monkeypatch.setattr(gateway_module, "get_provider_credentials", credentials)
    monkeypatch.setattr(settings, "AI_GATEWAY_MODE", "live")
    monkeypatch.setenv("AI_GATEWAY_MODE", "live")
    yield _FAKE_CREDENTIALS
    _FAKE_CREDENTIALS.clear()


_FAKE_CREDENTIALS: dict[str, tuple] = {}


def _credential() -> tuple:
    # Shape-compatible with backend.key_rotator.ProviderCredential; the value is a
    # constant that is never sent anywhere because adapters are faked below.
    return (type("Cred", (), {"value": "placeholder-for-test", "key_id": "TEST_KEY"})(),)


def _user(db) -> User:
    user = User(
        identifier=f"route-{uuid.uuid4().hex}",
        email=f"route-{uuid.uuid4().hex}@example.com",
        hashed_password="hash",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_fresh_database_with_openrouter_key_is_routable(no_network, db_session):
    no_network["OpenRouter"] = _credential()
    assert db_session.query(ProviderModel).count() == 0
    routes = AIGateway().eligible_routes("chat", db=db_session)
    assert [(route.provider, route.model) for route in routes] == [
        ("openrouter", settings.OPENROUTER_MODEL)
    ]


def test_fresh_database_without_keys_raises_meaningful_error(no_network, db_session):
    gateway = AIGateway()
    assert gateway.eligible_routes("chat", db=db_session) == ()
    with pytest.raises(gateway_module.GatewayUnavailableError) as raised:
        gateway.route("chat", db=db_session)
    message = str(raised.value)
    assert "No eligible free AI provider" in message
    # The message explains what to configure and never contains a key value.
    assert "OPENROUTER_KEY_1" in message
    assert "placeholder-for-test" not in message


def test_database_row_overrides_the_static_registry(no_network, db_session):
    no_network["OpenRouter"] = _credential()
    provider = Provider(name="openrouter", enabled=True, free_only=True)
    db_session.add(provider)
    db_session.commit()
    # The database row is an ADDITION here (different model id); only the same
    # (provider, model) key replaces a static entry -- see the merge test below.
    db_session.add(
        ProviderModel(
            provider_id=provider.id,
            model="admin/verified-free-model",
            is_free=True,
            capabilities=["chat"],
            enabled=True,
            availability="available",
            verified_at=datetime.now(timezone.utc),
        )
    )
    db_session.commit()
    models = [route.model for route in AIGateway().eligible_routes("chat", db=db_session)]
    assert "admin/verified-free-model" in models
    assert settings.OPENROUTER_MODEL in models


def test_database_row_can_disable_a_static_model(no_network, db_session):
    no_network["OpenRouter"] = _credential()
    provider = db_session.query(Provider).filter_by(name="openrouter").one_or_none()
    if provider is None:
        provider = Provider(name="openrouter", enabled=True, free_only=True)
        db_session.add(provider)
        db_session.commit()
    else:
        provider.enabled = True
        db_session.commit()
    db_session.query(ProviderModel).filter_by(provider_id=provider.id).delete(synchronize_session=False)
    db_session.commit()
    db_session.add(
        ProviderModel(
            provider_id=provider.id,
            model=settings.OPENROUTER_MODEL,
            is_free=True,
            capabilities=["chat"],
            enabled=False,
            availability="available",
        )
    )
    db_session.commit()
    assert AIGateway().eligible_routes("chat", db=db_session) == ()


def test_partial_database_does_not_hide_other_static_providers(no_network, db_session):
    no_network["OpenRouter"] = _credential()
    no_network["Gemini"] = _credential()
    # Remove a prior test's disabled override so this represents a partial DB
    # containing only Groq, not a disabled OpenRouter.
    db_session.query(ProviderModel).delete(synchronize_session=False)
    db_session.query(Provider).delete(synchronize_session=False)
    db_session.commit()
    provider = Provider(name="groq", enabled=True, free_only=True)
    db_session.add(provider)
    db_session.commit()
    db_session.add(
        ProviderModel(
            provider_id=provider.id, model="groq/verified", is_free=True, capabilities=["chat"],
            enabled=True, availability="available", verified_at=datetime.now(timezone.utc),
        )
    )
    db_session.commit()
    providers = {route.provider for route in AIGateway().eligible_routes("chat", db=db_session)}
    assert {"openrouter", "gemini"} <= providers


def test_groq_is_never_enabled_without_a_model_or_key(no_network, db_session, monkeypatch):
    monkeypatch.setattr(settings, "GROQ_MODEL", "")
    no_network["Groq"] = _credential()  # a key alone is not a usable route
    # This scenario has only a Groq DB row; clear provider rows from prior cases.
    db_session.query(ProviderModel).delete(synchronize_session=False)
    db_session.query(Provider).delete(synchronize_session=False)
    db_session.commit()
    provider = db_session.query(Provider).filter_by(name="groq").one_or_none()
    if provider is None:
        provider = Provider(name="groq", enabled=True, free_only=True)
        db_session.add(provider)
        db_session.commit()
    else:
        provider.enabled = True
        db_session.commit()
    db_session.query(ProviderModel).filter_by(provider_id=provider.id).delete(synchronize_session=False)
    db_session.commit()
    # No model row: a credential by itself must not activate Groq.
    routes = AIGateway().eligible_routes("chat", db=db_session)
    assert all(route.provider != "groq" for route in routes)


def test_placeholder_keys_are_not_treated_as_configured(monkeypatch, db_session):
    # Real key_rotator behaviour: a "replace-with-..." value is ignored.
    from backend import key_rotator

    monkeypatch.setenv("OPENROUTER_KEY_1", "replace-with-your-openrouter-key")
    for name in ("OPENROUTER_KEY_2", "OPENROUTER_KEY_3"):
        monkeypatch.delenv(name, raising=False)
    assert key_rotator.get_provider_credentials("OpenRouter") == ()


def test_primary_failure_falls_back_once_and_commits_once(no_network, db_session, monkeypatch):
    no_network["OpenRouter"] = _credential()
    no_network["Gemini"] = _credential()
    # Use only the explicit test registry for this fallback scenario.
    user = _user(db_session)
    calls: list[str] = []

    def fake_provider(route):
        class ProviderAdapter:
            def generate(self, prompt, *, system_prompt=None, model=None):
                calls.append(route.provider)
                if route.provider == "openrouter":
                    raise GatewayProviderError("capacity", status_code=503, retryable=True)
                return GatewayResponse("secondary answer", route.provider, route.model)

        return ProviderAdapter()

    gateway = AIGateway(registry=(
        registry_module.ModelSpec("openrouter", settings.OPENROUTER_MODEL, True, True, frozenset({"chat"}), availability="available", verified_at=datetime.now(timezone.utc)),
        registry_module.ModelSpec("gemini", settings.GEMINI_MODEL, True, True, frozenset({"chat"}), availability="available", verified_at=datetime.now(timezone.utc)),
    ))
    gateway.set_order(["openrouter", "gemini"])
    monkeypatch.setattr(gateway, "provider", fake_provider)
    request_id = f"fallback-{uuid.uuid4().hex}"
    answer, route = gateway.generate_with_quota(
        db_session, user_id=user.id, request_id=request_id, prompt="hello"
    )
    assert answer == "secondary answer"
    assert route.provider == "gemini"
    assert calls == ["openrouter", "gemini"]  # exactly one fallback, never a retry loop
    logs = db_session.query(UsageLog).filter_by(request_id=request_id).all()
    assert len(logs) == 1 and logs[0].units == 1


def test_no_route_means_no_reservation_and_no_usage(no_network, db_session):
    user = _user(db_session)
    request_id = f"none-{uuid.uuid4().hex}"
    with pytest.raises(gateway_module.GatewayUnavailableError):
        AIGateway().generate_with_quota(
            db_session, user_id=user.id, request_id=request_id, prompt="hello"
        )
    assert db_session.query(UsageLog).filter_by(request_id=request_id).count() == 0
    assert QuotaService().pending_for(db_session, user_id=user.id) == 0


def test_registry_merge_is_keyed_by_provider_and_model():
    static = (
        registry_module.ModelSpec("openrouter", "a", True, True, frozenset({"chat"})),
        registry_module.ModelSpec("gemini", "b", True, True, frozenset({"chat"})),
    )
    override = (registry_module.ModelSpec("gemini", "b", False, True, frozenset({"chat"}), source="database"),)
    merged = {spec.key: spec for spec in registry_module.merge_registry(static, override)}
    assert merged[("openrouter", "a")].enabled is True
    assert merged[("gemini", "b")].enabled is False


def test_model_ids_are_centralised_and_never_invented():
    from app.core import models as model_config

    openrouter = model_config.provider_model("openrouter")
    assert openrouter.model == settings.OPENROUTER_MODEL
    gemini = model_config.provider_model("gemini")
    assert gemini.model == settings.GEMINI_MODEL
    # No repository default exists for Groq, so an unset value must stay unset.
    groq = model_config.provider_model("groq")
    assert groq.configured == bool((settings.GROQ_MODEL or "").strip())


def test_dev_mode_uses_the_mock_provider_without_network(monkeypatch):
    monkeypatch.setattr(gateway_module.settings, "AI_GATEWAY_MODE", "dev")
    gateway = AIGateway()
    assert gateway.mode == "dev"
    assert [(r.provider, r.model) for r in gateway.eligible_routes("chat")] == [("mock", "mock-chat")]
