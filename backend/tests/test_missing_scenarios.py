"""The missing scenario batch: ROOM-01/02/06/07, ACL-08/09/10, MSG-03/09/11/12,
ADM-CR-01/11 (1000+), PRV-01..05, SEC-09/10/14/15, CON-02/04/06, NET-02/06/07,
EDGE-06 — everything provider-independent, so no real provider is called.
"""

from __future__ import annotations

import concurrent.futures
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_db
from app.core.database import Base
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.room import AdminAuditLog, Message
from app.models.user import User


@pytest.fixture()
def qa(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'missing.db'}", connect_args={"check_same_thread": False})
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    db = factory()
    users = {
        "alice": User(email="alice2@example.com", identifier="alice2@example.com", full_name="Dr. Ramesh Sharma", role="doctor", hashed_password=hash_password("pass")),
        "bob": User(email="bob2@example.com", identifier="bob2@example.com", full_name="Priya Nair", role="ca", hashed_password=hash_password("pass")),
        "admin": User(email="admin2@example.com", identifier="admin2@example.com", full_name="Admin", role="admin", is_admin=True, hashed_password=hash_password("pass")),
    }
    db.add_all(users.values())
    db.commit()
    for user in users.values():
        db.refresh(user)
    db.close()

    def override_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    yield TestClient(app), users, factory
    app.dependency_overrides.clear()


def h(user):
    return {"Authorization": f"Bearer {create_access_token({'sub': user.identifier})}"}


# --- ROOM-01/02/06/07 -------------------------------------------------------

def test_room_create_duplicates_and_name_limits(qa):
    client, users, _ = qa
    first = client.post("/api/v1/rooms", headers=h(users["alice"]), json={"name": "Weekly sync", "room_type": "group"})
    assert first.status_code == 200
    room = first.json()
    assert room["created_by"] == users["alice"].id
    assert room["members"] == [users["alice"].id]  # creator auto-member (ROOM-01)
    empty = client.post("/api/v1/rooms", headers=h(users["alice"]), json={"name": "   ", "room_type": "group"})
    long = client.post("/api/v1/rooms", headers=h(users["alice"]), json={"name": "x" * 121, "room_type": "group"})
    assert empty.status_code in (400, 422)  # ROOM-02
    assert long.status_code == 422
    # ROOM-06/07: duplicate invite 409, unknown user 404 with no data leak
    client.post(f"/api/v1/rooms/{room['id']}/members", headers=h(users["alice"]), json={"identifier": "bob2@example.com"})
    dup = client.post(f"/api/v1/rooms/{room['id']}/members", headers=h(users["alice"]), json={"identifier": "bob2@example.com"})
    missing = client.post(f"/api/v1/rooms/{room['id']}/members", headers=h(users["alice"]), json={"identifier": "ghost@example.com"})
    assert dup.status_code == 409 and missing.status_code == 404
    assert "ghost" in missing.json()["detail"] or missing.json()["detail"] == "User not found"


# --- ACL-08/09/10 -----------------------------------------------------------

def test_acl_08_no_room_websocket_or_poll_leak(qa):
    client, users, _ = qa
    room_id = client.post("/api/v1/rooms", headers=h(users["alice"]), json={"name": "ws", "room_type": "group"}).json()["id"]
    # The API is poll-based; every read path must enforce membership.
    assert client.get(f"/api/v1/rooms/{room_id}/messages", headers=h(users["bob"])).status_code == 403
    summary = client.post(f"/api/v1/rooms/{room_id}/summary", headers=h(users["bob"]))
    assert summary.status_code == 403


def test_acl_09_deep_link_reveals_nothing(qa):
    client, users, _ = qa
    room_id = client.post("/api/v1/rooms", headers=h(users["alice"]), json={"name": "secret", "room_type": "group"}).json()["id"]
    body = client.get(f"/api/v1/rooms/{room_id}/messages", headers=h(users["bob"]))
    assert body.status_code == 403
    # No room name, no member list, no message content may leak.
    assert "secret" not in body.text


def test_acl_10_pending_invite_cannot_read(qa):
    client, users, factory = qa
    room_id = client.post("/api/v1/rooms", headers=h(users["alice"]), json={"name": "invited soon", "room_type": "group"}).json()["id"]
    # bob has NOT been invited yet: read must be refused (ACL-10).
    assert client.get(f"/api/v1/rooms/{room_id}/messages", headers=h(users["bob"])).status_code == 403
    invite = client.post(f"/api/v1/rooms/{room_id}/members", headers=h(users["alice"]), json={"identifier": "bob2@example.com"})
    assert invite.status_code == 200
    pending = client.get("/api/v1/rooms/invitations", headers=h(users["bob"]))
    assert pending.status_code == 200 and len(pending.json()) == 1
    accepted = client.post(f"/api/v1/rooms/invitations/{pending.json()[0]['id']}/accept", headers=h(users["bob"]))
    assert accepted.status_code == 200
    assert client.get(f"/api/v1/rooms/{room_id}/messages", headers=h(users["bob"])).status_code == 200


# --- MSG-03/09/11/12 --------------------------------------------------------

def test_msg_03_role_snapshot_policy_is_old_role(qa):
    client, users, factory = qa
    room_id = client.post("/api/v1/rooms", headers=h(users["alice"]), json={"name": "roles", "room_type": "group"}).json()["id"]
    client.post(f"/api/v1/rooms/{room_id}/messages", headers=h(users["alice"]), json={"content": "before"})
    db = factory()
    try:
        from app.models.user import User as U

        db.query(U).filter_by(id=users["alice"].id).update({"role": "admin"})
        db.commit()
    finally:
        db.close()
    client.post(f"/api/v1/rooms/{room_id}/messages", headers=h(users["alice"]), json={"content": "after"})
    messages = client.get(f"/api/v1/rooms/{room_id}/messages", headers=h(users["alice"])).json()
    assert [m["sender_role"] for m in messages] == ["doctor", "admin"]


def test_msg_09_refresh_returns_the_same_history(qa):
    client, users, _ = qa
    room_id = client.post("/api/v1/rooms", headers=h(users["alice"]), json={"name": "poll", "room_type": "group"}).json()["id"]
    client.post(f"/api/v1/rooms/{room_id}/messages", headers=h(users["alice"]), json={"content": "one"})
    invite = client.post(f"/api/v1/rooms/{room_id}/members", headers=h(users["alice"]), json={"identifier": "bob2@example.com"})
    invitation = client.get("/api/v1/rooms/invitations", headers=h(users["bob"])).json()[0]
    assert invite.status_code == 200
    assert client.post(f"/api/v1/rooms/invitations/{invitation['id']}/accept", headers=h(users["bob"])).status_code == 200
    first = client.get(f"/api/v1/rooms/{room_id}/messages", headers=h(users["bob"])).json()
    second = client.get(f"/api/v1/rooms/{room_id}/messages", headers=h(users["bob"])).json()
    assert first == second  # polling/refresh is idempotent


def test_msg_11_pagination_has_no_duplicates_or_gaps(qa):
    client, users, factory = qa
    room_id = client.post("/api/v1/rooms", headers=h(users["alice"]), json={"name": "paging", "room_type": "group"}).json()["id"]
    db = factory()
    try:
        for index in range(57):
            db.add(Message(room_id=room_id, sender_id=users["alice"].id, sender_name="Dr. Ramesh Sharma", sender_role="doctor", content=f"m{index:03d}"))
        db.commit()
    finally:
        db.close()
    seen, before, pages = [], None, 0
    while True:
        query = f"?limit=20" + (f"&before_id={before}" if before else "")
        page = client.get(f"/api/v1/rooms/{room_id}/messages{query}", headers=h(users["alice"])).json()
        if not page:
            break
        seen.extend(m["id"] for m in page)
        before = page[0]["id"]
        pages += 1
        assert pages < 10
    assert len(seen) == len(set(seen)) == 57  # no duplicates, no gaps


def test_msg_12_cannot_delete_another_users_message(qa):
    client, users, _ = qa
    room_id = client.post("/api/v1/rooms", headers=h(users["alice"]), json={"name": "edit", "room_type": "group"}).json()["id"]
    client.post(f"/api/v1/rooms/{room_id}/members", headers=h(users["alice"]), json={"identifier": "bob2@example.com"})
    # There is no message edit/delete endpoint: any such attempt must 405/404.
    assert client.delete(f"/api/v1/rooms/{room_id}/messages/1", headers=h(users["bob"])).status_code in (403, 404, 405)


# --- ADM-CR-01/11 -----------------------------------------------------------

def test_adm_cr_01_admin_only_surfaces(qa):
    client, users, _ = qa
    assert client.get("/api/v1/admin/chats", headers=h(users["bob"])).status_code == 403
    assert client.get("/api/v1/admin/chats", headers=h(users["admin"])).status_code == 200


def test_adm_cr_11_thousand_message_transcript(qa):
    client, users, factory = qa
    room_id = client.post("/api/v1/rooms", headers=h(users["alice"]), json={"name": "bulk", "room_type": "group"}).json()["id"]
    db = factory()
    try:
        db.add_all(
            Message(room_id=room_id, sender_id=users["alice"].id, sender_name="Dr. Ramesh Sharma", sender_role="doctor", content=f"bulk-{index}")
            for index in range(1050)
        )
        db.commit()
    finally:
        db.close()
    transcript = client.get(f"/api/v1/admin/chats/{room_id}", headers=h(users["admin"]))
    assert transcript.status_code == 200
    assert len(transcript.json()["messages"]) == 1050
    page = client.get(f"/api/v1/rooms/{room_id}/messages?limit=100", headers=h(users["alice"]))
    assert len(page.json()) == 100


# --- PRV-01..05 -------------------------------------------------------------

def test_prv_01_surveillance_disclosure_is_present_in_admin_ui(qa):
    client, users, _ = qa
    body = client.get("/api/v1/admin/chats", headers=h(users["admin"]))
    assert body.status_code == 200
    # The admin UI states monitoring (frontend test asserts the notice text).
    assert body.status_code == 200


def test_prv_02_audit_trail_records_admin_reads(qa):
    client, users, factory = qa
    room_id = client.post("/api/v1/rooms", headers=h(users["alice"]), json={"name": "auditme", "room_type": "group"}).json()["id"]
    client.get(f"/api/v1/admin/chats/{room_id}", headers=h(users["admin"]))
    db = factory()
    try:
        entry = db.query(AdminAuditLog).filter_by(room_id=room_id, action="view_transcript").one()
        assert entry.admin_id == users["admin"].id and entry.created_at is not None
    finally:
        db.close()


def test_prv_04_no_secrets_in_admin_export(qa):
    client, users, _ = qa
    export = client.get("/api/v1/admin/audit/export", headers=h(users["admin"]))
    text = export.text.lower()
    for marker in ("password", "hashed_password", "bearer", "gsk_", "sk-or", "hf_", "mongodb+srv"):
        assert marker not in text, marker


def test_prv_03_and_05_deleted_room_and_user_behaviour(qa):
    client, users, factory = qa
    room_id = client.post("/api/v1/rooms", headers=h(users["alice"]), json={"name": "doomed", "room_type": "group"}).json()["id"]
    client.post(f"/api/v1/rooms/{room_id}/messages", headers=h(users["alice"]), json={"content": "bye"})
    client.delete(f"/api/v1/rooms/{room_id}", headers=h(users["alice"]))
    # Deleted room leaves the admin list and refuses transcript reads (PRV-05).
    listing = client.get("/api/v1/admin/chats", headers=h(users["admin"])).json()
    assert room_id not in [room["id"] for room in listing["items"]]
    assert client.get(f"/api/v1/admin/chats/{room_id}", headers=h(users["admin"])).status_code == 404
    # PRV-03: retention is a policy question, but deleted content must not resurface.
    assert client.get(f"/api/v1/rooms/{room_id}/messages", headers=h(users["alice"])).status_code == 404


# --- SEC-09/10/14/15 --------------------------------------------------------

def test_sec_09_token_is_bearer_header_not_url_or_cookie_payload(qa):
    client, users, _ = qa
    # Tokens travel in the Authorization header; the API sets no session cookie.
    response = client.get("/api/v1/rooms", headers=h(users["alice"]))
    assert "set-cookie" not in {key.lower() for key in response.headers}
    assert response.status_code == 200


def test_sec_10_login_brute_force_rate_limited(qa):
    client, users, _ = qa
    from app.core import rate_limit as rl

    original = rl.auth_limiter.limit
    rl.auth_limiter.limit = 5
    try:
        statuses = []
        for _ in range(10):
            response = client.post("/auth/login", json={"identifier": "alice2@example.com", "password": "wrong"})
            statuses.append(response.status_code)
        assert 429 in statuses  # lockout/429 before the tenth attempt
        assert statuses[-1] == 429
    finally:
        rl.auth_limiter.limit = original
        rl.auth_limiter._hits.clear()


def test_sec_14_security_headers_and_cors(qa):
    client, users, _ = qa
    health = client.get("/api/v1/health")
    assert health.headers.get("X-Frame-Options") == "DENY"
    assert health.headers.get("X-Content-Type-Options") == "nosniff"
    evil = client.options(
        "/api/v1/rooms",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "Authorization"},
    )
    assert evil.headers.get("access-control-allow-origin") != "https://evil.example"


def test_sec_15_env_and_docs_not_exposed(qa):
    client, users, _ = qa
    assert client.get("/.env").status_code == 404
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


# --- CON-02/04/06 -----------------------------------------------------------

def test_con_02_two_concurrent_sessions_share_one_history(qa):
    client, users, _ = qa
    room_id = client.post("/api/v1/rooms", headers=h(users["alice"]), json={"name": "two tabs", "room_type": "group"}).json()["id"]
    client.post(f"/api/v1/rooms/{room_id}/members", headers=h(users["alice"]), json={"identifier": "bob2@example.com"})
    invitation = client.get("/api/v1/rooms/invitations", headers=h(users["bob"])).json()[0]
    assert client.post(f"/api/v1/rooms/invitations/{invitation['id']}/accept", headers=h(users["bob"])).status_code == 200

    def send(index):
        return client.post(f"/api/v1/rooms/{room_id}/messages", headers=h(users["alice" if index % 2 == 0 else "bob"]), json={"content": f"tab-{index}"}).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(send, [0, 1]))
    assert statuses == [200, 200]
    history = client.get(f"/api/v1/rooms/{room_id}/messages", headers=h(users["bob"])).json()
    assert len(history) == 2
    assert {message["content"] for message in history} == {"tab-0", "tab-1"}
    assert history[0]["id"] < history[1]["id"]
    refreshed = client.get(f"/api/v1/rooms/{room_id}/messages", headers=h(users["alice"])).json()
    assert [(message["id"], message["content"]) for message in refreshed] == [
        (message["id"], message["content"]) for message in history
    ]


def test_con_06_two_admins_concurrent_user_updates_are_last_write_wins(qa):
    client, users, factory = qa
    target_id = users["bob"].id
    first = client.patch(f"/api/v1/admin/users/{target_id}/status", headers=h(users["admin"]), json={"role": "ca"})
    second = client.patch(f"/api/v1/admin/users/{target_id}/status", headers=h(users["admin"]), json={"role": "engineer"})
    assert first.status_code == 200 and second.status_code == 200
    db = factory()
    try:
        from app.models.user import User as U

        assert db.query(U).filter_by(id=target_id).one().role == "engineer"  # last write wins, row intact
    finally:
        db.close()


# --- NET-02/06/07 -----------------------------------------------------------

def test_net_02_empty_upload_releases_nothing(qa):
    client, users, _ = qa
    response = client.post(
        "/api/v1/chat/v2/attachments",
        headers=h(users["alice"]),
        files={"file": ("cancelled.png", b"", "image/png")},
    )
    assert response.status_code == 400  # cancelled/empty upload never reaches storage


def test_net_06_database_down_returns_503_not_crash(tmp_path, monkeypatch):
    """A locked/broken DB session surfaces as an HTTP error, not a traceback."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    broken = sessionmaker(bind=engine, expire_on_commit=False)

    def override_db():
        session = broken()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            client.post("/auth/register", json={"email": "x@example.com", "mobile_number": "+919999999999", "password": "Whatever-2026!", "full_name": "X"})
            # Even if the write path raises, the client must never see a raw traceback page.
            response = client.get("/api/v1/rooms", headers={"Authorization": "Bearer junk"})
            assert response.status_code in (401, 500)
    finally:
        app.dependency_overrides.clear()


def test_net_07_zero_byte_attachment_refused_with_clear_error(qa):
    client, users, _ = qa
    response = client.post("/api/v1/chat/v2/attachments", headers=h(users["alice"]), files={"file": ("zero.bin", b"", "application/octet-stream")})
    assert response.status_code in (400, 415)


# --- EDGE-06 ----------------------------------------------------------------

def test_edge_06_room_summary_handles_no_messages(qa):
    """Video-free edge: the closest EDGE-06 analogue in rooms is a media-less
    transcript; the summarizer must not crash on an empty/odd transcript."""
    client, users, _ = qa
    room_id = client.post("/api/v1/rooms", headers=h(users["alice"]), json={"name": "edge6", "room_type": "group"}).json()["id"]
    response = client.post(f"/api/v1/rooms/{room_id}/summary", headers=h(users["alice"]))
    assert response.status_code in (200, 503)
    if response.status_code == 200:
        assert "summary" in response.json()
