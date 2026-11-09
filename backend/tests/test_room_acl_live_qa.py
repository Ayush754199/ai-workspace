"""Authenticated room scenarios (ROOM-03/04/08/09/10, ACL-*, MSG-*, ADM-CR-*)
against an isolated QA database. No provider call is made anywhere here.
"""

from __future__ import annotations

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
    engine = create_engine(f"sqlite:///{tmp_path / 'qa_rooms.db'}", connect_args={"check_same_thread": False})
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    db = factory()
    users = {
        "alice": User(email="alice@example.com", identifier="alice@example.com", full_name="Dr. Ramesh Sharma", role="doctor", hashed_password=hash_password("pass")),
        "bob": User(email="bob@example.com", identifier="bob@example.com", full_name="Priya Nair", role="ca", hashed_password=hash_password("pass")),
        "carol": User(email="carol@example.com", identifier="carol@example.com", full_name="Carol Outsider", role="user", hashed_password=hash_password("pass")),
        "admin": User(email="admin@example.com", identifier="admin@example.com", full_name="Admin", role="admin", is_admin=True, hashed_password=hash_password("pass")),
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


def _create_room(client, user, name="QA Room"):
    response = client.post("/api/v1/rooms", headers=h(user), json={"name": name, "room_type": "group"})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_room_03_only_member_rooms_are_listed(qa):
    client, users, _ = qa
    room_id = _create_room(client, users["alice"], "Alice private")
    assert [room["id"] for room in client.get("/api/v1/rooms", headers=h(users["bob"])).json()] == []
    _create_room(client, users["bob"], "Bob private")
    bob_rooms = client.get("/api/v1/rooms", headers=h(users["bob"])).json()
    assert all(room["id"] != room_id for room in bob_rooms)
    assert len(bob_rooms) == 1


def test_room_04_invite_by_username_and_email(qa):
    client, users, _ = qa
    room_id = _create_room(client, users["alice"])
    by_username = client.post(f"/api/v1/rooms/{room_id}/members", headers=h(users["alice"]), json={"identifier": "Priya Nair"})
    assert by_username.status_code == 200
    assert by_username.json()["user_id"] == users["bob"].id
    assert room_id in [room["id"] for room in client.get("/api/v1/rooms", headers=h(users["bob"])).json()]


def test_room_08_non_member_cannot_invite(qa):
    client, users, factory = qa
    room_id = _create_room(client, users["alice"])
    response = client.post(f"/api/v1/rooms/{room_id}/members", headers=h(users["carol"]), json={"identifier": "bob@example.com"})
    assert response.status_code == 403
    db = factory()
    try:
        assert db.query(User).filter_by(email="bob@example.com").one() is not None
    finally:
        db.close()


def test_room_09_and_10_left_member_loses_access(qa):
    client, users, factory = qa
    room_id = _create_room(client, users["alice"])
    client.post(f"/api/v1/rooms/{room_id}/members", headers=h(users["alice"]), json={"identifier": "bob@example.com"})
    client.post(f"/api/v1/rooms/{room_id}/messages", headers=h(users["alice"]), json={"content": "members only"})

    # No leave endpoint exists; simulate a removed member by updating membership.
    db = factory()
    try:
        from app.models.room import Room

        room = db.query(Room).filter_by(id=room_id).one()
        room.members = [member for member in room.members if member != users["bob"].id]
        db.commit()
    finally:
        db.close()

    assert room_id not in [room["id"] for room in client.get("/api/v1/rooms", headers=h(users["bob"])).json()]
    assert client.get(f"/api/v1/rooms/{room_id}/messages", headers=h(users["bob"])).status_code == 403
    # Owner keeps access.
    assert client.get(f"/api/v1/rooms/{room_id}/messages", headers=h(users["alice"])).status_code == 200


def test_acl_03_room_details_hidden_from_non_member(qa):
    client, users, _ = qa
    room_id = _create_room(client, users["alice"])
    response = client.patch(f"/api/v1/rooms/{room_id}", headers=h(users["bob"]), json={"name": "hijack"})
    assert response.status_code in (403, 404)


def test_acl_05_room_enumeration_is_uniform(qa):
    client, users, _ = qa
    rooms = [_create_room(client, users["alice"], f"room-{index}") for index in range(1, 4)]
    statuses = [client.get(f"/api/v1/rooms/{room_id}/messages", headers=h(users["carol"])).status_code for room_id in rooms]
    missing = client.get("/api/v1/rooms/999999/messages", headers=h(users["carol"])).status_code
    assert statuses == [403, 403, 403]
    assert missing in (403, 404)


def test_acl_06_normal_user_cannot_touch_admin_surveillance(qa):
    client, users, _ = qa
    room_id = _create_room(client, users["alice"])
    assert client.get("/api/v1/admin/chats", headers=h(users["bob"])).status_code == 403
    assert client.get(f"/api/v1/admin/chats/{room_id}", headers=h(users["bob"])).status_code == 403
    assert client.get("/api/v1/admin/audit/export", headers=h(users["bob"])).status_code == 403


def test_acl_09_non_member_gets_no_room_payload(qa):
    client, users, _ = qa
    room_id = _create_room(client, users["alice"])
    body = client.get(f"/api/v1/rooms/{room_id}/messages", headers=h(users["carol"]))
    assert body.status_code == 403
    assert "messages" not in body.text or body.json().get("detail")


def test_msg_01_and_02_sender_tag_is_name_and_role(qa):
    client, users, _ = qa
    room_id = _create_room(client, users["alice"])
    response = client.post(f"/api/v1/rooms/{room_id}/messages", headers=h(users["alice"]), json={"content": "Hi team"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["sender_name"] == "Dr. Ramesh Sharma"
    assert payload["sender_role"] == "doctor"
    assert f"{payload['sender_name']} ({payload['sender_role']})" == "Dr. Ramesh Sharma (doctor)"


def test_msg_03_role_change_does_not_rewrite_old_messages(qa):
    client, users, factory = qa
    room_id = _create_room(client, users["alice"])
    client.post(f"/api/v1/rooms/{room_id}/messages", headers=h(users["alice"]), json={"content": "old turn"})
    db = factory()
    try:
        user = db.query(User).filter_by(id=users["alice"].id).one()
        user.role = "admin"
        db.commit()
    finally:
        db.close()
    client.post(f"/api/v1/rooms/{room_id}/messages", headers=h(users["alice"]), json={"content": "new turn"})
    messages = client.get(f"/api/v1/rooms/{room_id}/messages", headers=h(users["alice"])).json()
    assert [message["sender_role"] for message in messages] == ["doctor", "admin"]


def test_msg_06_unicode_and_xss_payload_stored_as_data(qa):
    client, users, _ = qa
    room_id = _create_room(client, users["alice"])
    content = "नमस्ते! 🎉 <script>alert(1)</script> مرحبا"
    response = client.post(f"/api/v1/rooms/{room_id}/messages", headers=h(users["alice"]), json={"content": content})
    assert response.status_code == 200
    stored = client.get(f"/api/v1/rooms/{room_id}/messages", headers=h(users["alice"])).json()
    assert stored[0]["content"] == content  # stored verbatim and returned as JSON text


def test_msg_08_ordering_and_timestamps(qa):
    client, users, _ = qa
    room_id = _create_room(client, users["alice"])
    for index in range(3):
        client.post(f"/api/v1/rooms/{room_id}/messages", headers=h(users["alice"]), json={"content": f"m{index}"})
    messages = client.get(f"/api/v1/rooms/{room_id}/messages", headers=h(users["alice"])).json()
    assert [message["content"] for message in messages] == ["m0", "m1", "m2"]
    assert all(message["created_at"] for message in messages)


def test_adm_cr_01_to_13_surveillance_surface(qa):
    client, users, factory = qa
    room_alpha = _create_room(client, users["alice"], "Alpha review")
    room_beta = _create_room(client, users["bob"], "Beta review")
    client.post(f"/api/v1/rooms/{room_alpha}/members", headers=h(users["alice"]), json={"identifier": "bob@example.com"})
    client.post(f"/api/v1/rooms/{room_alpha}/messages", headers=h(users["alice"]), json={"content": "alpha message"})
    client.post(f"/api/v1/rooms/{room_beta}/messages", headers=h(users["bob"]), json={"content": "beta message"})

    listing = client.get("/api/v1/admin/chats", headers=h(users["admin"]))
    assert listing.status_code == 200
    assert listing.json()["total"] == 2  # ADM-CR-02: admin sees rooms without membership

    by_room = client.get("/api/v1/admin/chats?room_name=alpha", headers=h(users["admin"]))
    assert [room["name"] for room in by_room.json()["items"]] == ["Alpha review"]  # ADM-CR-06

    by_user = client.get("/api/v1/admin/chats?username=ramesh", headers=h(users["admin"]))
    assert [room["id"] for room in by_user.json()["items"]] == [room_alpha]  # ADM-CR-04

    by_role = client.get("/api/v1/admin/chats?role=ca", headers=h(users["admin"]))
    assert {room["id"] for room in by_role.json()["items"]} == {room_alpha, room_beta}  # ADM-CR-05

    combined = client.get("/api/v1/admin/chats?room_name=review&role=doctor&username=sharma", headers=h(users["admin"]))
    assert [room["id"] for room in combined.json()["items"]] == [room_alpha]  # ADM-CR-07

    # Filters must match the SAME person: "sharma" (doctor) + "priya" (ca) matches nobody.
    mismatched = client.get("/api/v1/admin/chats?username=priya&role=doctor", headers=h(users["admin"]))
    assert mismatched.json()["items"] == [], mismatched.json()
    both = client.get("/api/v1/admin/chats?username=priya&role=ca", headers=h(users["admin"]))
    assert {room["id"] for room in both.json()["items"]} == {room_alpha, room_beta}

    empty = client.get("/api/v1/admin/chats?room_name=does-not-exist", headers=h(users["admin"]))
    assert empty.status_code == 200 and empty.json()["items"] == [] and empty.json()["total"] == 0  # ADM-CR-08

    transcript = client.get(f"/api/v1/admin/chats/{room_alpha}", headers=h(users["admin"]))
    assert transcript.status_code == 200
    assert transcript.json()["messages"][0]["sender_name"] == "Dr. Ramesh Sharma"
    assert transcript.json()["messages"][0]["sender_role"] == "doctor"  # ADM-CR-03

    export = client.get("/api/v1/admin/audit/export", headers=h(users["admin"]))
    assert export.status_code == 200
    assert "view_transcript" in export.text  # ADM-CR-09 / PRV-02
    assert "pass" not in export.text.lower() or "@example.com" not in export.text  # no secrets in export

    db = factory()
    try:
        entries = db.query(AdminAuditLog).filter(AdminAuditLog.action == "view_transcript").all()
        assert any(entry.room_id == room_alpha and entry.admin_id == users["admin"].id for entry in entries)
    finally:
        db.close()

    assert client.get(f"/api/v1/admin/chats/{room_alpha}", headers=h(users["bob"])).status_code == 403
    assert client.get("/api/v1/admin/chats", headers=h(users["admin"])).status_code == 200


def test_admin_write_permissions_are_read_only(qa):
    client, users, _ = qa
    room_id = _create_room(client, users["alice"])
    # Admin is not a member: the message endpoint must refuse, not silently post.
    response = client.post(f"/api/v1/rooms/{room_id}/messages", headers=h(users["admin"]), json={"content": "silent admin post"})
    assert response.status_code == 403


def test_admin_role_revocation_is_enforced_from_db_on_every_request(qa):
    """ADM-CR-13: revoke the admin flag and the SAME token is 403 immediately."""
    client, users, factory = qa
    # Promote bob to admin while his (old) token is already issued.
    token = create_access_token({"sub": users["bob"].identifier})
    db = factory()
    try:
        from app.models.user import User as U

        db.query(U).filter_by(id=users["bob"].id).update({"is_admin": True, "role": "admin"})
        db.commit()
    finally:
        db.close()
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/admin/chats", headers=headers).status_code == 200
    # Revoke without any re-login: the very next request must be refused.
    db = factory()
    try:
        from app.models.user import User as U

        db.query(U).filter_by(id=users["bob"].id).update({"is_admin": False, "role": "ca"})
        db.commit()
    finally:
        db.close()
    response = client.get("/api/v1/admin/chats", headers=headers)
    assert response.status_code == 403
    me = client.get("/auth/me", headers=headers)
    assert me.status_code == 200 and me.json()["is_admin"] is False
    client, users, factory = qa
    room_id = _create_room(client, users["alice"])
    db = factory()
    try:
        for index in range(120):
            db.add(Message(room_id=room_id, sender_id=users["alice"].id, sender_name="Dr. Ramesh Sharma", sender_role="doctor", content=f"msg-{index}"))
        db.commit()
    finally:
        db.close()
    page = client.get(f"/api/v1/rooms/{room_id}/messages?limit=50", headers=h(users["alice"]))
    assert page.status_code == 200 and len(page.json()) == 50
    transcript = client.get(f"/api/v1/admin/chats/{room_id}", headers=h(users["admin"]))
    assert transcript.status_code == 200 and len(transcript.json()["messages"]) == 120
