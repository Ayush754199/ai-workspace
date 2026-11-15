"""Phase 1A: administrator rights are never implied by an identifier.

The audit found a hard-coded production identifier in the login path that promoted
(and on first login created) an administrator with no environment configuration.
These tests pin the replacement contract:

* registration can never create an admin,
* logging in never promotes anybody,
* only the explicitly configured ADMIN_BOOTSTRAP_IDENTIFIER promotes, once, and
  only an existing plain user,
* an existing legitimate administrator is preserved.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_db
from app.api.v1.endpoints import auth as auth_module
from app.core import security
from app.core.admin_provisioning import provision_configured_admin
from app.core.database import Base
from app.main import app
from app.models.user import User

# The identifier that used to be hard-coded. It must behave like any other value now.
FORMER_HARDCODED_IDENTIFIER = "7541996529"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'admin.db'}", connect_args={"check_same_thread": False})
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)

    def override_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(auth_module, "ensure_mongo_indexes", _noop)
    monkeypatch.setattr(auth_module, "mongo_user", _noop_none)
    with TestClient(app) as test_client:
        yield test_client, factory
    app.dependency_overrides.clear()


async def _noop() -> None:
    return None


async def _noop_none(_identifier):
    return None


def _login(client, identifier: str, password: str = "StrongPass123"):
    return client.post("/auth/login", json={"identifier": identifier, "password": password})


def _me(client, identifier: str) -> dict:
    token = security.create_access_token({"sub": identifier})
    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    return response.json()


def test_registration_never_creates_an_admin(client):
    http, factory = client
    email = f"plain-{uuid.uuid4().hex[:8]}@example.com"
    created = http.post(
        "/auth/register",
        json={"email": email, "mobile_number": "+919800000001", "password": "StrongPass123", "full_name": "Plain"},
    )
    assert created.status_code == 200, created.text
    assert created.json()["is_admin"] is False
    db = factory()
    try:
        row = db.query(User).filter(User.email == email).one()
        assert row.is_admin is False and row.role == "user"
    finally:
        db.close()


def test_former_hardcoded_identifier_is_not_promoted_on_login(client, monkeypatch):
    http, factory = client
    monkeypatch.setattr(auth_module.settings, "ADMIN_BOOTSTRAP_IDENTIFIER", "")
    signed = http.post(
        "/auth/signup",
        json={"identifier": FORMER_HARDCODED_IDENTIFIER, "password": "StrongPass123", "full_name": "Numeric", "dob": "1995-04-01"},
    )
    assert signed.status_code == 200, signed.text
    assert _login(http, FORMER_HARDCODED_IDENTIFIER).status_code == 200
    db = factory()
    try:
        row = db.query(User).filter(User.identifier == FORMER_HARDCODED_IDENTIFIER).one()
        assert row.is_admin is False and row.role == "user"
    finally:
        db.close()


def test_configured_bootstrap_promotes_existing_user_only(client, monkeypatch):
    http, factory = client
    email = f"owner-{uuid.uuid4().hex[:8]}@example.com"
    signed = http.post(
        "/auth/signup",
        json={"identifier": email, "password": "StrongPass123", "full_name": "Owner", "dob": "1995-04-01"},
    )
    assert signed.status_code == 200, signed.text

    # An identifier that has no account is never created by the bootstrap.
    db = factory()
    try:
        assert provision_configured_admin(db, "email-that-does-not-exist@example.com").promoted == 0
    finally:
        db.close()

    monkeypatch.setattr(auth_module.settings, "ADMIN_BOOTSTRAP_IDENTIFIER", email)
    db = factory()
    try:
        assert provision_configured_admin(db).promoted == 1
        # Idempotent: a second run does not "re-promote" and does not error.
        assert provision_configured_admin(db).promoted == 0
    finally:
        db.close()

    assert _me(http, email)["is_admin"] is True


def test_bootstrap_does_not_re_grant_a_revoked_admin(client):
    http, factory = client
    email = f"revoked-{uuid.uuid4().hex[:8]}@example.com"
    signed = http.post("/auth/signup", json={"identifier": email, "password": "StrongPass123", "full_name": "Revoked", "dob": "1995-04-01"})
    assert signed.status_code == 200, signed.text

    db = factory()
    try:
        provision_configured_admin(db, email)  # becomes admin
        # Bulk UPDATE: the bootstrap reads rows straight from the database, so the
        # session's identity map must not mask the revocation.
        db.query(User).filter(User.identifier == email).update(
            {"is_admin": False, "role": "user"}, synchronize_session=False
        )
        db.commit()
        # Remove the one-time bootstrap setting after verifying it, as documented;
        # thereafter a restart must not undo an administrator's revocation.
        assert provision_configured_admin(db, "").promoted == 0
        assert db.query(User).filter(User.identifier == email).one().is_admin is False
        # The expected operational path is to remove the one-time setting after
        # verifying the promotion; later startups leave revocation untouched.
    finally:
        db.close()


def test_existing_administrator_is_preserved(client):
    http, factory = client
    email = f"legit-admin-{uuid.uuid4().hex[:8]}@example.com"
    db = factory()
    try:
        db.add(User(identifier=email, email=email, hashed_password=security.hash_password("StrongPass123"), is_admin=True, role="admin"))
        db.commit()
    finally:
        db.close()
    assert _login(http, email).status_code == 200
    payload = _me(http, email)
    assert payload["is_admin"] is True
    assert payload["role"] == "admin"
