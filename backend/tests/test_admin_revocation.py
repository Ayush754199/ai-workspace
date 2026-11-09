"""ADM-CR-13: revoking an admin must lock their unexpired token immediately."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_db
from app.core.database import Base
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.user import User


@pytest.fixture()
def qa(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'revoke.db'}", connect_args={"check_same_thread": False})
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    db = factory()
    admin = User(email="boss@example.com", identifier="boss@example.com", full_name="Boss", role="admin", is_admin=True, hashed_password=hash_password("pass"))
    db.add(admin)
    db.commit()
    db.refresh(admin)
    token = create_access_token({"sub": "boss@example.com"})
    db.close()

    def override_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    yield TestClient(app), factory, token
    app.dependency_overrides.clear()


def test_revoked_admin_is_rejected_on_the_next_request(qa):
    client, factory, token = qa
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/admin/chats", headers=headers).status_code == 200

    db = factory()
    try:
        db.query(User).filter_by(email="boss@example.com").update({"is_admin": False, "role": "user"})
        db.commit()
    finally:
        db.close()

    response = client.get("/api/v1/admin/chats", headers=headers)
    assert response.status_code == 403
    assert response.json()["detail"] == "Administrator access required"


def test_revoked_admin_is_rejected_on_auth_me(qa):
    client, factory, token = qa
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/auth/me", headers=headers).status_code == 200

    db = factory()
    try:
        db.query(User).filter_by(email="boss@example.com").update({"is_admin": False, "role": "user"})
        db.commit()
    finally:
        db.close()

    profile = client.get("/auth/me", headers=headers)
    assert profile.status_code == 200  # still authenticated
    assert profile.json()["is_admin"] is False
    assert profile.json()["role"] == "user"
    # ...and the admin surface is closed on the very next call.
    assert client.get("/api/v1/admin/chats", headers=headers).status_code == 403


def test_disabled_user_is_rejected_immediately(qa):
    client, factory, token = qa
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/auth/me", headers=headers).status_code == 200

    db = factory()
    try:
        db.query(User).filter_by(email="boss@example.com").update({"is_active": False})
        db.commit()
    finally:
        db.close()

    assert client.get("/auth/me", headers=headers).status_code == 401
    assert client.get("/api/v1/admin/chats", headers=headers).status_code == 401
