"Regression: requesting web search must not silently fall back to unsourced AI."""

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.api.deps import get_db
from app.core.database import Base
from app.core.security import create_access_token
from app.main import app
from app.models.user import User
from app.models.chat import AiConversation
from app.services import research as research_service

def test_web_search_failure_is_explicit_and_does_not_call_chat(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'research_failure.db'}", connect_args={"check_same_thread": False})
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    db = factory()
    user = User(email="web-failure@example.com", identifier="web-failure@example.com", full_name="Web Failure", hashed_password="hash")
    db.add(user)
    db.commit()
    db.refresh(user)
    conversation = AiConversation(user_id=user.id, title="Research")
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    headers = {"Authorization": f"Bearer {create_access_token({'sub': user.identifier})}"}

    def override_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    called = {"chat": False}
    # The test isolates retrieval failure; use the deterministic mock route so
    # provider availability does not mask the search error being asserted.
    monkeypatch.setattr("app.services.ai_gateway.gateway.settings.AI_GATEWAY_MODE", "dev")

    def broken_search(*_args, **_kwargs):
        raise research_service.SearchUnavailable("network unavailable")

    async def forbidden_chat(*_args, **_kwargs):
        called["chat"] = True
        return "unsourced", "fake", "fake"

    monkeypatch.setattr(research_service, "search_sources", broken_search)
    monkeypatch.setattr("app.api.v1.endpoints.chat_v2.generate_reply", forbidden_chat)
    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/chat/v2/conversations/{conversation.id}/messages",
                headers=headers,
                json={"content": "latest official Python release", "web_search": True},
            )
        assert response.status_code == 503
        assert "web search is unavailable" in response.json()["detail"].lower()
        assert called["chat"] is False
        from app.models.gateway import QuotaReservation
        check = factory()
        try:
            reservation = check.query(QuotaReservation).filter_by(user_id=user.id).one()
            assert reservation.state == "FAILED"
        finally:
            check.close()
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_table_from_csv_preserves_quoted_commas_and_special_characters():
    from app.services.documents import table_from_text
    parsed = table_from_text('Name,Product,Amount\n"Doe, Jane",Café,12.50\n')
    assert parsed == [{"Name": "Doe, Jane", "Product": "Café", "Amount": "12.50"}]
