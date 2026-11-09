import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_db
from app.core.database import Base
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.room import AdminAuditLog, Message, Room
from app.models.user import User


@pytest.fixture()
def client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'rooms.db'}", connect_args={"check_same_thread": False})
    # The fixture closes its session but the test then reads user attributes, so keep
    # loaded instances from expiring on commit.
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    db = session_factory()
    users = [
        User(email="one@example.com", full_name="One User", role="doctor", hashed_password=hash_password("pass")),
        User(email="two@example.com", full_name="Two User", role="ca", hashed_password=hash_password("pass")),
        User(email="admin@example.com", full_name="Admin", role="admin", is_admin=True, hashed_password=hash_password("pass")),
    ]
    db.add_all(users)
    db.commit()
    db.refresh(users[0])
    db.refresh(users[1])
    db.refresh(users[2])
    room = Room(name="Private", room_type="private", members=[users[0].id], created_by=users[0].id)
    db.add(room)
    db.commit()
    db.refresh(room)
    db.close()

    def override_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    yield TestClient(app), users, room
    app.dependency_overrides.clear()


def auth(user):
    return {"Authorization": f"Bearer {create_access_token({'sub': user.email})}"}


def test_non_member_cannot_see_or_use_room(client):
    api, users, room = client
    assert api.get("/api/v1/rooms", headers=auth(users[1])).json() == []
    assert api.get(f"/api/v1/rooms/{room.id}/messages", headers=auth(users[1])).status_code == 403
    assert api.post(f"/api/v1/rooms/{room.id}/messages", headers=auth(users[1]), json={"content": "no"}).status_code == 403


def test_message_snapshot_and_invites(client):
    api, users, room = client
    search = api.get("/api/v1/rooms/users/search?q=two", headers=auth(users[0]))
    assert search.status_code == 200
    assert [(item["id"], item["email"]) for item in search.json()] == [(users[1].id, "two@example.com")]
    response = api.post(f"/api/v1/rooms/{room.id}/messages", headers=auth(users[0]), json={"content": "hello"})
    assert response.status_code == 200
    assert response.json()["sender_name"] == "One User"
    assert response.json()["sender_role"] == "doctor"
    assert api.post(f"/api/v1/rooms/{room.id}/members", headers=auth(users[0]), json={"identifier": "two@example.com"}).status_code == 200
    assert api.post(f"/api/v1/rooms/{room.id}/members", headers=auth(users[0]), json={"identifier": "two@example.com"}).status_code == 409
    assert api.post(f"/api/v1/rooms/{room.id}/members", headers=auth(users[0]), json={"identifier": "missing@example.com"}).status_code == 404


def test_blank_room_message_is_rejected(client):
    api, users, room = client
    response = api.post(f"/api/v1/rooms/{room.id}/messages", headers=auth(users[0]), json={"content": " \n\t "})
    assert response.status_code == 422
    assert response.json()["detail"] == "Message cannot be empty"


def test_single_member_room_round_trips_as_list(client):
    """Regression: SQLite returned a bare int and the owner was locked out."""
    api, users, room = client
    create = api.post("/api/v1/rooms", headers=auth(users[1]), json={"name": "One member", "room_type": "chat"})
    assert create.status_code == 200
    new_id = create.json()["id"]
    assert create.json()["members"] == [users[1].id]
    own_messages = api.get(f"/api/v1/rooms/{new_id}/messages", headers=auth(users[1]))
    assert own_messages.status_code == 200, own_messages.text
    listed = api.get("/api/v1/rooms", headers=auth(users[1])).json()
    assert new_id in [item["id"] for item in listed]
    sent = api.post(f"/api/v1/rooms/{new_id}/messages", headers=auth(users[1]), json={"content": "hi"})
    assert sent.status_code == 200, sent.text


def test_admin_can_inspect_and_audit(client):
    api, users, room = client
    assert api.get("/api/v1/admin/chats", headers=auth(users[1])).status_code == 403
    response = api.get(f"/api/v1/admin/chats/{room.id}", headers=auth(users[2]))
    assert response.status_code == 200
    db = next(app.dependency_overrides[get_db]())
    try:
        assert db.query(AdminAuditLog).filter(AdminAuditLog.room_id == room.id).count() == 1
    finally:
        db.close()


def test_room_invitation_accept_decline_remove_and_leave(client):
    api, users, room = client
    sent = api.post(f"/api/v1/rooms/{room.id}/members", headers=auth(users[0]), json={"identifier": "two@example.com"})
    assert sent.status_code == 200 and sent.json()["status"] == "pending"
    invitations = api.get("/api/v1/rooms/invitations", headers=auth(users[1]))
    assert invitations.status_code == 200 and len(invitations.json()) == 1
    invitation_id = invitations.json()[0]["id"]
    assert api.post(f"/api/v1/rooms/invitations/{invitation_id}/accept", headers=auth(users[1])).status_code == 200
    assert api.delete(f"/api/v1/rooms/{room.id}/members/{users[1].id}", headers=auth(users[0])).status_code == 200

    sent_again = api.post(f"/api/v1/rooms/{room.id}/members", headers=auth(users[0]), json={"identifier": "two@example.com"})
    invitation_id = api.get("/api/v1/rooms/invitations", headers=auth(users[1])).json()[0]["id"]
    assert sent_again.status_code == 200
    assert api.post(f"/api/v1/rooms/invitations/{invitation_id}/decline", headers=auth(users[1])).status_code == 200

    accepted_again = api.post(f"/api/v1/rooms/{room.id}/members", headers=auth(users[0]), json={"identifier": "two@example.com"})
    invitation_id = api.get("/api/v1/rooms/invitations", headers=auth(users[1])).json()[0]["id"]
    assert accepted_again.status_code == 200
    assert api.post(f"/api/v1/rooms/invitations/{invitation_id}/accept", headers=auth(users[1])).status_code == 200
    assert api.delete(f"/api/v1/rooms/{room.id}/membership", headers=auth(users[1])).status_code == 200
