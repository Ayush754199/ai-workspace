"""ADM-10: with every provider disabled the API says so and never fabricates a reply."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_db
from app.core.database import Base
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.gateway import UsageLog, UserQuota
from app.models.user import User
from app.services.ai_gateway import gateway as gateway_module


@pytest.fixture()
def no_provider(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'adm10.db'}", connect_args={"check_same_thread": False})
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    db = factory()
    db.add(User(email="noprov@example.com", identifier="noprov@example.com", full_name="No Provider", hashed_password=hash_password("pass")))
    db.commit()
    db.close()

    def override_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(gateway_module.settings, "AI_GATEWAY_MODE", "live")
    monkeypatch.setattr(gateway_module.settings, "OPENROUTER_KEY_1", "")
    monkeypatch.setattr(gateway_module, "get_provider_credentials", lambda name: ())
    headers = {"Authorization": f"Bearer {create_access_token({'sub': 'noprov@example.com'})}"}
    yield TestClient(app), headers, factory
    app.dependency_overrides.clear()


def test_models_endpoint_reports_no_provider_clearly(no_provider):
    client, headers, _ = no_provider
    response = client.get("/api/v1/chat/v2/models", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    auto = payload["models"][0]
    assert auto["id"] == "auto"
    assert auto["selectable"] is False
    assert "No eligible free provider" in auto["reason"]
    # No enabled/selectable route is advertised.
    assert not any(model["selectable"] for model in payload["models"])
    assert all("fake" not in str(model.get("label", "")).lower() for model in payload["models"])


def test_chat_send_returns_503_instead_of_fake_answer(no_provider):
    client, headers, factory = no_provider
    created = client.post("/api/v1/chat/v2/conversations", headers=headers, json={"title": "adm10"})
    conversation_id = created.json()["id"]
    response = client.post(f"/api/v1/chat/v2/conversations/{conversation_id}/messages", headers=headers, json={"content": "hello"})
    assert response.status_code == 503
    detail = response.json()["detail"].lower()
    assert "no eligible free ai provider" in detail or "no provider" in detail
    # Nothing was stored and no quota was consumed.
    stored = client.get(f"/api/v1/chat/v2/conversations/{conversation_id}", headers=headers).json()
    assert stored["messages"] == []
    db = factory()
    try:
        assert db.query(UserQuota).count() == 0
        assert db.query(UsageLog).count() == 0
    finally:
        db.close()


def test_stream_reports_error_event_not_invented_text(no_provider):
    client, headers, _ = no_provider
    created = client.post("/api/v1/chat/v2/conversations", headers=headers, json={"title": "adm10 stream"})
    conversation_id = created.json()["id"]
    response = client.post(f"/api/v1/chat/v2/conversations/{conversation_id}/stream", headers=headers, json={"content": "hello"})
    # resolve_model fails before streaming starts, which must be a clear 503.
    assert response.status_code == 503
    assert "no eligible" in response.json()["detail"].lower()
