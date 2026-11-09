import io
import uuid

from fastapi.testclient import TestClient

from app.main import app


def test_legacy_image_endpoint_rejects_invalid_image_without_500_or_quota_use(monkeypatch):
    from app.api.v1.endpoints import chat as chat_endpoint
    from app.api import deps
    from app.core.database import SessionLocal
    from app.models.gateway import CapabilityQuota
    from app.models.user import User

    monkeypatch.setattr(chat_endpoint.settings, "LEGACY_DIRECT_AI_ENABLED", True)
    monkeypatch.setattr(deps.settings, "LEGACY_DIRECT_AI_ENABLED", True)
    monkeypatch.setattr(chat_endpoint.settings, "HF_IMAGE_API_URL", "https://example.invalid/image")
    monkeypatch.setattr(chat_endpoint.settings, "HF_IMAGE_EDIT_SPACE", "https://example.invalid/edit")
    # Patch THIS module's name, not a global: another test in the suite patches
    # app.services.ai_gateway.gateway.get_provider_credentials, and undoing that
    # here (or vice versa) made this test depend on execution order.
    monkeypatch.setattr(chat_endpoint, "get_provider_credentials", lambda _provider: (object(),), raising=True)
    # The legacy route must not be disabled by an environment left over from a
    # previous test process.
    assert chat_endpoint.settings.LEGACY_DIRECT_AI_ENABLED is True

    identifier = f"invalid-image-{uuid.uuid4().hex}@example.com"
    # Mobile numbers are unique in the schema. Only 10_000 combinations used to be
    # generated (``uuid % 10000``), so a suite run large enough to fill that space
    # collided and failed registration with "already registered". Use 9 digits, and
    # stay within the 15-digit limit enforced by normalize_mobile_number.
    mobile = f"+919876{uuid.uuid4().int % 1_000_000_000:09d}"
    with TestClient(app) as client:
        registered = client.post(
            "/auth/register",
            json={
                "email": identifier,
                "mobile_number": mobile,
                "password": "StrongPass123",
                "full_name": "Invalid Image Tester",
            },
        )
        assert registered.status_code == 200, registered.text
        login = client.post("/auth/login", json={"identifier": identifier, "password": "StrongPass123"})
        assert login.status_code == 200, login.text
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        response = client.post(
            "/api/v1/chat/image",
            headers=headers,
            files={"image": ("payload.png", io.BytesIO(b"not-an-image"), "image/png")},
            data={"prompt": "Make the background blue"},
        )

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "Invalid image file"
    assert "Traceback" not in response.text

    with SessionLocal() as db:
        user = db.query(User).filter_by(email=identifier).one()
        quota = db.query(CapabilityQuota).filter_by(user_id=user.id, capability="image").one()
        assert quota.daily_used == 0
        assert quota.reserved == 0
