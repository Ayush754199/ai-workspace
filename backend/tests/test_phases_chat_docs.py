"""Phase 1-11 regression tests: chat workspace, documents, office, images,
research, voice, gateway routing, quota and rate limiting.

All AI calls run against the mock gateway (AI_GATEWAY_MODE=dev) so the suite
never spends provider quota or touches a paid API.
"""

from __future__ import annotations

import io
import pytest

from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers(client):
    response = client.post(
        "/auth/register",
        json={
            "email": "phase-tester@example.com",
            "mobile_number": "+919876500000",
            "password": "StrongPass123",
            "full_name": "Phase Tester",
        },
    )
    assert response.status_code in {200, 400}
    login = client.post("/auth/login", json={"identifier": "phase-tester@example.com", "password": "StrongPass123"})
    assert login.status_code == 200
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _dev_gateway(monkeypatch):
    monkeypatch.setattr("app.services.ai_gateway.gateway.settings.AI_GATEWAY_MODE", "dev")
    from app.services.ai_gateway import gateway as gateway_module

    gateway_module.settings.AI_GATEWAY_MODE = "dev"
    yield


@pytest.fixture(autouse=True)
def _fresh_ai_quota(reset_ai_quota):
    """Give every test in this module its own quota window.

    The ledger is persistent SQLite state: the module makes many mock AI calls
    against a single user, so a previous run (or a manual live run) could leave
    that user at the daily limit and turn a valid assertion into HTTP 429. The
    configured limits are untouched -- only the counters are cleared.
    """
    reset_ai_quota("phase-tester@example.com")
    yield


def _new_conversation(client, headers, title="Test chat"):
    response = client.post("/api/v1/chat/v2/conversations", json={"title": title}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_models_endpoint_exposes_only_verified_routes(client, auth_headers):
    response = client.get("/api/v1/chat/v2/models", headers=auth_headers)
    assert response.status_code == 200
    models = response.json()["models"]
    assert models[0]["id"] == "auto"
    assert any(model["provider"] == "mock" for model in models)


def test_chat_roundtrip_persists_history_and_serves_context(client, auth_headers):
    conversation_id = _new_conversation(client, auth_headers)
    first = client.post(
        f"/api/v1/chat/v2/conversations/{conversation_id}/messages",
        json={"content": "Explain vector search", "model": "auto"},
        headers=auth_headers,
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["message"]["role"] == "assistant"
    assert body["message"]["content"].startswith("[mock:")
    assert body["message"]["provider"] == "mock"

    second = client.post(
        f"/api/v1/chat/v2/conversations/{conversation_id}/messages",
        json={"content": "And the tradeoffs?"},
        headers=auth_headers,
    )
    assert second.status_code == 200
    # context is server-side: the second prompt contains the first turn
    assert "Explain vector search" in second.json()["message"]["content"]

    detail = client.get(f"/api/v1/chat/v2/conversations/{conversation_id}", headers=auth_headers).json()
    assert len(detail["messages"]) == 4


def test_regenerate_replaces_assistant_message(client, auth_headers):
    conversation_id = _new_conversation(client, auth_headers)
    sent = client.post(
        f"/api/v1/chat/v2/conversations/{conversation_id}/messages",
        json={"content": "Hello"},
        headers=auth_headers,
    ).json()
    reply_id = sent["message"]["id"]
    regenerated = client.post(
        f"/api/v1/chat/v2/conversations/{conversation_id}/regenerate",
        json={"message_id": reply_id},
        headers=auth_headers,
    )
    assert regenerated.status_code == 200
    assert regenerated.json()["message"]["id"] == reply_id


def test_streaming_returns_chunks_and_persists_message(client, auth_headers):
    conversation_id = _new_conversation(client, auth_headers)
    with client.stream(
        "POST",
        f"/api/v1/chat/v2/conversations/{conversation_id}/stream",
        json={"content": "Stream this answer"},
        headers=auth_headers,
    ) as response:
        assert response.status_code == 200
        payload = "".join(response.iter_text())
    assert "chunk" in payload
    assert "[DONE]" in payload
    stored = client.get(f"/api/v1/chat/v2/conversations/{conversation_id}", headers=auth_headers).json()
    assert any(message["role"] == "assistant" and message["content"] for message in stored["messages"])


def test_conversation_is_private_to_its_owner(client, auth_headers):
    conversation_id = _new_conversation(client, auth_headers)
    other = client.post(
        "/auth/register",
        json={
            "email": "other-phase@example.com",
            "mobile_number": "+919876500001",
            "password": "StrongPass123",
            "full_name": "Other",
        },
    )
    assert other.status_code in {200, 400}
    token = client.post("/auth/login", json={"identifier": "other-phase@example.com", "password": "StrongPass123"}).json()["access_token"]
    forbidden = client.get(f"/api/v1/chat/v2/conversations/{conversation_id}", headers={"Authorization": f"Bearer {token}"})
    assert forbidden.status_code == 404


def test_attachment_upload_extracts_text(client, auth_headers):
    response = client.post(
        "/api/v1/chat/v2/attachments",
        files={"file": ("notes.txt", io.BytesIO(b"Refund policy is 30 days."), "text/plain")},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["has_text"] is True
    assert "Refund policy" in body["preview"]


def test_attachment_runs_when_used_in_a_message(client, auth_headers):
    attachment = client.post(
        "/api/v1/chat/v2/attachments",
        files={"file": ("notes.txt", io.BytesIO(b"Budget is 5000 USD."), "text/plain")},
        headers=auth_headers,
    ).json()
    conversation_id = _new_conversation(client, auth_headers)
    response = client.post(
        f"/api/v1/chat/v2/conversations/{conversation_id}/messages",
        json={"content": "What is the budget?", "attachment_ids": [attachment["id"]]},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert "Budget is 5000 USD" in response.json()["message"]["content"]


def test_unsupported_attachment_type_is_rejected(client, auth_headers):
    response = client.post(
        "/api/v1/chat/v2/attachments",
        files={"file": ("hack.exe", io.BytesIO(b"MZ"), "application/x-msdownload")},
        headers=auth_headers,
    )
    assert response.status_code == 415


@pytest.mark.parametrize("fmt", ["pdf", "docx", "pptx", "xlsx"])
def test_document_generation_produces_real_files(client, auth_headers, fmt):
    content = "# Report\n\n- First point\n- Second point\n\n| name | value |\n| --- | --- |\n| alpha | 1 |\n| beta | 2 |"
    response = client.post(
        "/api/v1/documents/generate",
        json={"title": "Report", "content": content, "format": fmt, "filename": "report"},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    assert len(response.content) > 60
    assert response.headers["content-disposition"].endswith(f'.{fmt}"')


def test_csv_generation_returns_csv(client, auth_headers):
    content = "| name | value |\n| --- | --- |\n| alpha | 1 |\n| beta | 2 |"
    response = client.post(
        "/api/v1/documents/generate",
        json={"title": "Data", "content": content, "format": "csv", "filename": "data"},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    assert b"alpha" in response.content and b"beta" in response.content
    assert response.headers["content-disposition"].endswith('.csv"')


def test_document_extract_and_ask(client, auth_headers):
    extract = client.post(
        "/api/v1/documents/extract",
        files={"file": ("memo.txt", io.BytesIO(b"Invoice total is 42 USD."), "text/plain")},
        headers=auth_headers,
    )
    assert extract.status_code == 200
    assert "42 USD" in extract.json()["text"]

    ask = client.post(
        "/api/v1/documents/ask",
        files={"file": ("memo.txt", io.BytesIO(b"Invoice total is 42 USD."), "text/plain")},
        data={"question": "What is the total?"},
        headers=auth_headers,
    )
    assert ask.status_code == 200
    assert ask.json()["answer"]


def test_document_type_is_validated(client, auth_headers):
    response = client.post(
        "/api/v1/documents/extract",
        files={"file": ("script.sh", io.BytesIO(b"echo hi"), "text/x-shellscript")},
        headers=auth_headers,
    )
    assert response.status_code == 415


def test_image_background_removal_returns_transparent_png(client, auth_headers):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (40, 40), (255, 255, 255)).save(buffer, format="PNG")
    response = client.post(
        "/api/v1/images/remove-background",
        files={"file": ("white.png", buffer.getvalue(), "image/png")},
        data={"tolerance": "20"},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    result = Image.open(io.BytesIO(response.content))
    assert result.mode == "RGBA"
    assert result.getpixel((0, 0))[3] == 0


def test_image_enhance_returns_jpeg(client, auth_headers):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (60, 60), (120, 90, 60)).save(buffer, format="PNG")
    response = client.post(
        "/api/v1/images/enhance",
        files={"file": ("in.png", buffer.getvalue(), "image/png")},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.content[:2] == b"\xff\xd8"


def test_image_describe_returns_real_facts(client, auth_headers):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (64, 32), (10, 20, 30)).save(buffer, format="PNG")
    response = client.post(
        "/api/v1/images/describe",
        files={"file": ("small.png", buffer.getvalue(), "image/png")},
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["width"] == 64 and body["height"] == 32


def test_voice_status_reports_configured_engines(client, auth_headers):
    response = client.get("/api/v1/voice/status", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert set(body["stt"]) == {"groq", "gemini"}


def test_voice_transcribe_fails_loudly_without_provider(client, auth_headers, monkeypatch):
    import app.api.v1.endpoints.voice as voice_module

    monkeypatch.setattr(voice_module, "_groq_keys", lambda: ())
    monkeypatch.setattr(voice_module, "_gemini_keys", lambda: ())
    response = client.post(
        "/api/v1/voice/transcribe",
        files={"file": ("a.webm", io.BytesIO(b"\x00" * 64), "audio/webm")},
        headers=auth_headers,
    )
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"].lower()


def test_office_generate_returns_docx(client, auth_headers):
    response = client.post(
        "/api/v1/office/generate",
        json={"prompt": "A short proposal", "format": "docx", "title": "Proposal"},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert response.headers["X-AI-Provider"] == "mock"


def test_gateway_falls_back_to_next_route_when_first_fails(monkeypatch):
    from app.services.ai_gateway import AIGateway
    from app.services.ai_gateway.gateway import Route
    from app.services.ai_gateway.providers import GatewayProviderError, MockProvider

    gateway = AIGateway()
    monkeypatch.setattr(gateway, "eligible_routes", lambda capability="chat", db=None: (Route("broken", "m1"), Route("mock", "mock-chat")))

    class Broken:
        name = "broken"

        def generate(self, prompt, *, system_prompt=None, model=None):
            raise GatewayProviderError("upstream 503", retryable=True)

    monkeypatch.setattr(gateway, "provider", lambda route: Broken() if route.provider == "broken" else MockProvider())
    text, route = gateway.generate("hi")
    assert route.provider == "mock"
    assert "hi" in text


def test_gateway_does_not_retry_uncertain_failures(monkeypatch):
    from app.services.ai_gateway import AIGateway
    from app.services.ai_gateway.gateway import Route
    from app.services.ai_gateway.providers import GatewayProviderError

    gateway = AIGateway()
    monkeypatch.setattr(gateway, "eligible_routes", lambda capability="chat", db=None: (Route("a", "m"), Route("b", "m")))
    calls = []

    class Failing:
        def generate(self, prompt, *, system_prompt=None, model=None):
            calls.append(1)
            raise GatewayProviderError("timeout", uncertain=True)

    monkeypatch.setattr(gateway, "provider", lambda route: Failing())
    with pytest.raises(GatewayProviderError):
        gateway.generate("hi")
    assert len(calls) == 1


def test_per_user_concurrency_limit_blocks_second_request():
    from app.services.chat_service import ConcurrencyLimiter

    limiter = ConcurrencyLimiter(limit=1)
    assert limiter.acquire(7) is True
    assert limiter.acquire(7) is False
    limiter.release(7)
    assert limiter.acquire(7) is True


def test_quota_exhaustion_is_reported_not_faked(client, auth_headers, monkeypatch):
    from app.core.database import SessionLocal
    from app.models.gateway import UserQuota
    from app.models.user import User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "phase-tester@example.com").one()
        quota = db.query(UserQuota).filter(UserQuota.user_id == user.id).first()
        if quota is None:
            quota = UserQuota(user_id=user.id, daily_limit=1, per_chat_limit=20)
            db.add(quota)
        db.commit()
        conversation_id = _new_conversation(client, auth_headers)
        # total consumed today == remaining allowance, so the next reserve must fail
        quota = db.query(UserQuota).filter(UserQuota.user_id == user.id).one()
        quota.daily_used = quota.daily_limit
        quota.reserved = 0
        db.commit()
        response = client.post(
            f"/api/v1/chat/v2/conversations/{conversation_id}/messages",
            json={"content": "over quota"},
            headers=auth_headers,
        )
        assert response.status_code == 429
        assert "quota" in response.json()["detail"].lower()
    finally:
        db.close()


def test_auth_rate_limit_returns_429(client):
    from app.core import rate_limit as rl

    original = rl.auth_limiter.limit
    rl.auth_limiter.limit = 2
    rl.auth_limiter._hits.clear()
    try:
        for _ in range(3):
            response = client.post("/auth/login", json={"identifier": "x@example.com", "password": "wrong"})
        assert response.status_code == 429
        assert "retry-after" in {key.lower() for key in response.headers}
    finally:
        rl.auth_limiter.limit = original
        rl.auth_limiter._hits.clear()


def test_security_headers_present(client):
    response = client.get("/api/v1/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers.get("X-Request-Id")


def test_admin_endpoints_require_admin(client, auth_headers):
    # ``test_admin_can_set_gateway_order_and_toggle_features`` promotes this user
    # to admin on the shared database. Demote first so the check does not depend
    # on test execution order.
    from app.core.database import SessionLocal
    from app.models.user import User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "phase-tester@example.com").one()
        user.is_admin = False
        user.role = "user"
        db.commit()
    finally:
        db.close()

    for path in ("/api/v1/admin/providers", "/api/v1/admin/logs", "/api/v1/admin/analytics/usage", "/api/v1/admin/settings"):
        response = client.get(path, headers=auth_headers)
        assert response.status_code == 403, path


def test_admin_can_set_gateway_order_and_toggle_features(client):
    from app.core.database import SessionLocal
    from app.models.user import User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "phase-tester@example.com").one()
        user.is_admin = True
        db.commit()
    finally:
        db.close()
    token = client.post("/auth/login", json={"identifier": "phase-tester@example.com", "password": "StrongPass123"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    order = client.put("/api/v1/admin/gateway/order", json={"order": ["groq", "gemini"]}, headers=headers)
    assert order.status_code == 200
    assert order.json()["order"] == ["groq", "gemini"]

    rejected = client.put("/api/v1/admin/gateway/order", json={"order": ["openai-paid"]}, headers=headers)
    assert rejected.status_code == 400

    toggle = client.put("/api/v1/admin/settings/features", json={"key": "research", "enabled": False}, headers=headers)
    assert toggle.status_code == 200
    assert toggle.json()["features"]["research"] is False

def test_admin_toggle_actually_disables_a_feature(client):
    """Toggling a feature off must stop it answering, not just change a label."""
    from app.core.database import SessionLocal
    from app.models.user import User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "phase-tester@example.com").one()
        user.is_admin = True
        db.commit()
    finally:
        db.close()
    token = client.post("/auth/login", json={"identifier": "phase-tester@example.com", "password": "StrongPass123"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    client.put("/api/v1/admin/settings/features", json={"key": "research", "enabled": False}, headers=headers)
    blocked = client.post("/api/v1/research/search", json={"question": "hello world"}, headers=headers)
    assert blocked.status_code == 503
    assert "disabled" in blocked.json()["detail"].lower()

    client.put("/api/v1/admin/settings/features", json={"key": "research", "enabled": True}, headers=headers)


def test_groq_and_openrouter_keys_are_rotated_like_other_providers(monkeypatch):
    from backend import key_rotator

    monkeypatch.setenv("GROQ_API_KEY_1", "gsk-one")
    monkeypatch.setenv("GROQ_API_KEY_2", "gsk-two")
    monkeypatch.setenv("OPENROUTER_KEY_1", "sk-or-one")
    monkeypatch.setenv("OPENROUTER_KEY_2", "sk-or-two")
    credentials = key_rotator.get_provider_credentials("Groq")
    ids = [credential.key_id for credential in credentials]
    assert "GROQ_API_KEY_1" in ids and "GROQ_API_KEY_2" in ids
    assert not any("gsk" in credential.key_id for credential in credentials)
    openrouter_credentials = key_rotator.get_provider_credentials("OpenRouter")
    assert [credential.key_id for credential in openrouter_credentials] == [
        "OPENROUTER_KEY_1", "OPENROUTER_KEY_2"
    ]
    assert [credential.value for credential in openrouter_credentials] == ["sk-or-one", "sk-or-two"]


def test_groq_is_inside_the_free_allow_list():
    from app.core.config import settings

    assert {"gemini", "groq", "openrouter", "huggingface"} <= set(settings.FREE_PROVIDERS)
    assert "openai" not in settings.FREE_PROVIDERS

def test_legacy_direct_provider_endpoints_are_disabled(client, auth_headers):
    """The old endpoints skip quota/policy, so they must answer 410 by default."""
    for path, body in (("/api/v1/ai/generate", {"prompt": "hello"}),):
        response = client.post(path, json=body, headers=auth_headers)
        assert response.status_code == 410, response.text
    legacy = client.post("/api/v1/chat/stream", json={"prompt": "hi", "session_id": "x"}, headers=auth_headers)
    assert legacy.status_code == 410


def test_private_and_internal_urls_are_blocked_for_outbound_fetch():
    from app.core.netguard import UnsafeURLError, assert_safe_url

    blocked = [
        "http://127.0.0.1/admin",
        "http://localhost:8000",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "file:///etc/passwd",
        "ftp://example.com/x",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://service.local/",
    ]
    for url in blocked:
        with pytest.raises(UnsafeURLError):
            assert_safe_url(url)


def test_secret_redaction_covers_provider_key_formats():
    from backend.logger import _safe_text

    # Synthetic, non-functional fixtures: real provider keys must never be
    # committed to the repository, including in test data.
    samples = [
        "gsk_" + "a" * 52,
        "sk-or-v1-" + "b" * 64,
        "hf_" + "c" * 34,
        "ltxv_" + "d" * 43,
    ]
    for sample in samples:
        safe = _safe_text(f"error while calling provider with {sample}")
        assert sample not in safe
        assert "REDACTED" in safe


def test_me_falls_back_to_sql_when_mongo_is_unreachable(monkeypatch):
    """A Mongo outage must not log users out or turn /auth/me into a 500."""
    from fastapi.testclient import TestClient

    from app.api.v1.endpoints import auth as auth_module
    from app.core import security
    from app.main import app

    async def boom(_identifier):
        raise RuntimeError("mongo down")

    monkeypatch.setattr(auth_module, "mongo_user", boom)
    token = security.create_access_token(data={"sub": "phase-tester@example.com"})
    with TestClient(app) as probe:
        response = probe.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    assert response.json()["email"] == "phase-tester@example.com"


def test_log_payload_redacts_prompt_and_user_identifiers():
    from backend.logger import _safe_payload

    safe = _safe_payload({"prompt": "my secret prompt", "email": "a@b.com", "model": "llama", "status_code": 429})
    assert safe["prompt"].startswith("[REDACTED")
    assert safe["email"].startswith("[REDACTED")
    assert safe["model"] == "llama"
    assert safe["status_code"] == 429


def test_video_capabilities_report_real_configuration(client, auth_headers):
    response = client.get("/api/v1/video/capabilities", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert set(body) >= {"feature_enabled", "user_permitted", "text_to_video", "image_to_video"}
    # Unconfigured spaces must report ready=false, never a fake ready state.
    if not body["text_to_video"]["space"]:
        assert body["text_to_video"]["ready"] is False


def test_admin_verify_rejects_providers_outside_the_free_list(client):
    from app.core.database import SessionLocal
    from app.models.user import User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "phase-tester@example.com").one()
        user.is_admin = True
        db.commit()
    finally:
        db.close()
    token = client.post("/auth/login", json={"identifier": "phase-tester@example.com", "password": "StrongPass123"}).json()["access_token"]
    response = client.post(
        "/api/v1/admin/providers/verify",
        json={"provider": "openai", "model": "gpt-4o", "capability": "chat"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400
    assert "allow-list" in response.json()["detail"]
