from copy import deepcopy
from datetime import datetime, timedelta, timezone
from io import BytesIO
import re

import pytest
from PIL import Image
from bson import ObjectId

import streamlit_app
from backend.auth_security import (
    SECURITY_QUESTIONS,
    is_admin_bootstrap_authorized,
    normalize_security_answer,
    validate_username,
)


class _UpdateResult:
    matched_count = 1


class _FakeUsers:
    def __init__(self, account):
        self.account = deepcopy(account)

    def find_one(self, query, projection=None):
        if "_id" in query and query.get("_id") != self.account["_id"]:
            return None
        username_query = query.get("username", {})
        if isinstance(username_query, dict) and "$regex" in username_query:
            if not re.fullmatch(
                username_query["$regex"],
                self.account.get("username", ""),
                flags=re.IGNORECASE if username_query.get("$options") == "i" else 0,
            ):
                return None
        lock_query = query.get("password_reset_locked_until")
        if isinstance(lock_query, dict) and "$gt" in lock_query:
            locked_until = self.account.get("password_reset_locked_until")
            if not locked_until or locked_until <= lock_query["$gt"]:
                return None
        return deepcopy(self.account)

    def find_one_and_update(self, query, update, **_kwargs):
        if query.get("_id") != self.account["_id"]:
            return None
        self.account["password_reset_attempts"] = (
            self.account.get("password_reset_attempts", 0)
            + update["$inc"]["password_reset_attempts"]
        )
        return deepcopy(self.account)

    def update_one(self, query, update):
        if query.get("_id") != self.account["_id"]:
            return type("Result", (), {"matched_count": 0})()
        for condition in query.get("$or", []):
            exists_query = condition.get("password_reset_locked_until", {})
            locked_until = self.account.get("password_reset_locked_until")
            if "$exists" in exists_query and exists_query["$exists"] is False and locked_until is not None:
                continue
            if "$lte" in exists_query and locked_until is not None and locked_until > exists_query["$lte"]:
                continue
            break
        else:
            if "$or" in query:
                return type("Result", (), {"matched_count": 0})()
        for key, value in update.get("$set", {}).items():
            self.account[key] = value
        for key in update.get("$unset", {}):
            self.account.pop(key, None)
        return _UpdateResult()


class _FakeSessions:
    def __init__(self):
        self.records = {}

    def insert_one(self, record):
        self.records[record["_id"]] = deepcopy(record)

    def find_one(self, query):
        record = self.records.get(query.get("_id"))
        expiration = query.get("expires_at", {}).get("$gt")
        if record and expiration and record["expires_at"] <= expiration:
            return None
        return deepcopy(record)

    def delete_one(self, query):
        return self.records.pop(query.get("_id"), None)

    def delete_many(self, query):
        user_id = query.get("user_id")
        self.records = {
            token_hash: record
            for token_hash, record in self.records.items()
            if record["user_id"] != user_id
        }


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  AyUsH  ", "ayush"),
        ("admin", "admin"),
        ("Admin_01", "admin_01"),
    ],
)
def test_usernames_are_normalized_case_insensitively(raw, expected):
    assert validate_username(raw) == expected


def test_username_validation_rejects_short_or_unsupported_names():
    with pytest.raises(ValueError, match="3–32"):
        validate_username("ab")
    with pytest.raises(ValueError, match="3–32"):
        validate_username("name with spaces")


def test_reserved_admin_requires_configured_bootstrap_token():
    token = "long-random-bootstrap-token-with-32-chars"
    assert is_admin_bootstrap_authorized("ADMIN", token, token)
    assert is_admin_bootstrap_authorized("Ayush", token, token, admin_username="ayush")
    assert not is_admin_bootstrap_authorized("regular-user", token, token)
    assert not is_admin_bootstrap_authorized("admin", token, "")
    assert not is_admin_bootstrap_authorized("admin", "wrong", token)
    assert not is_admin_bootstrap_authorized("admin", "short", "short")


def test_security_answers_are_normalized_without_plaintext_storage():
    assert normalize_security_answer("  My   FIRST Pet  ") == "my first pet"
    assert SECURITY_QUESTIONS


def test_password_reset_hashes_answer_and_changes_password(monkeypatch):
    user_id = ObjectId()
    account = {
        "_id": user_id,
        "security_question": SECURITY_QUESTIONS[0],
        "security_answer_hash": streamlit_app.PASSWORD_HASHER.hash("blue dog"),
        "hashed_password": streamlit_app.PASSWORD_HASHER.hash("old-password"),
    }
    users = _FakeUsers(account)
    sessions = _FakeSessions()
    monkeypatch.setattr(streamlit_app, "get_users_collection", lambda: users)
    monkeypatch.setattr(streamlit_app, "get_auth_sessions_collection", lambda: sessions)

    streamlit_app.reset_password_with_security_answer(
        user_id,
        " BLUE   DOG ",
        "new-password",
    )

    streamlit_app.PASSWORD_HASHER.verify(users.account["hashed_password"], "new-password")
    assert users.account["security_answer_hash"] != "blue dog"
    assert "password_reset_attempts" not in users.account


def test_password_reset_locks_after_five_wrong_answers(monkeypatch):
    user_id = ObjectId()
    account = {
        "_id": user_id,
        "security_question": SECURITY_QUESTIONS[1],
        "security_answer_hash": streamlit_app.PASSWORD_HASHER.hash("correct answer"),
    }
    users = _FakeUsers(account)
    monkeypatch.setattr(streamlit_app, "get_users_collection", lambda: users)

    for _ in range(5):
        with pytest.raises(ValueError, match="incorrect"):
            streamlit_app.reset_password_with_security_answer(
                user_id,
                "wrong answer",
                "new-password",
            )

    assert "password_reset_locked_until" in users.account
    with pytest.raises(ValueError, match="Too many incorrect"):
        streamlit_app.reset_password_with_security_answer(
            user_id,
            "correct answer",
            "new-password",
        )


def test_password_reset_rejects_short_password_before_database_write(monkeypatch):
    def fail_if_called():
        raise AssertionError("Database should not be accessed for invalid password.")

    monkeypatch.setattr(streamlit_app, "get_users_collection", fail_if_called)
    with pytest.raises(ValueError, match="at least 8"):
        streamlit_app.reset_password_with_security_answer("account-3", "answer", "short")


def test_users_collection_enforces_case_insensitive_unique_username_index(monkeypatch):
    class FakeUsersCollection:
        def create_index(self, keys, **options):
            self.keys = keys
            self.options = options

    users = FakeUsersCollection()

    class FakeDatabase:
        def __getitem__(self, collection_name):
            assert collection_name == "users"
            return users

    class FakeClient:
        def __getitem__(self, database_name):
            assert database_name == streamlit_app.DB_NAME
            return FakeDatabase()

    monkeypatch.setattr(streamlit_app, "get_mongo_client", lambda: FakeClient())
    monkeypatch.setattr(streamlit_app, "_USER_INDEXES_READY", False)

    assert streamlit_app.get_users_collection() is users
    assert users.keys == [("username", 1)]
    assert users.options["unique"] is True
    assert users.options["collation"].document["strength"] == 2


def test_existing_account_can_configure_hashed_recovery_answer(monkeypatch):
    account = {
        "_id": "account-recovery",
        "username": "ayush",
        "hashed_password": streamlit_app.PASSWORD_HASHER.hash("current-password"),
        "password_reset_attempts": 2,
        "password_reset_locked_until": "old-lock",
    }
    users = _FakeUsers(account)
    monkeypatch.setattr(streamlit_app, "get_users_collection", lambda: users)

    streamlit_app.configure_password_recovery(
        "AYUSH",
        "current-password",
        SECURITY_QUESTIONS[0],
        "  First   Pet ",
    )

    assert users.account["security_question"] == SECURITY_QUESTIONS[0]
    assert users.account["security_answer_hash"] != "first pet"
    streamlit_app.PASSWORD_HASHER.verify(users.account["security_answer_hash"], "first pet")
    assert "password_reset_attempts" not in users.account
    assert "password_reset_locked_until" not in users.account


def test_existing_configured_account_can_be_promoted_with_token_and_password(monkeypatch):
    token = "a-private-administrator-bootstrap-token-123456"
    account = {
        "_id": "existing-admin",
        "username": "admin",
        "hashed_password": streamlit_app.PASSWORD_HASHER.hash("current-password"),
        "is_active": True,
        "role": "user",
        "is_admin": False,
    }
    users = _FakeUsers(account)
    monkeypatch.setattr(streamlit_app, "get_users_collection", lambda: users)
    monkeypatch.setattr(streamlit_app, "ADMIN_BOOTSTRAP_TOKEN", token)
    monkeypatch.setattr(streamlit_app, "ADMIN_USERNAME", "admin")

    streamlit_app.promote_account_to_admin("ADMIN", "current-password", token)

    assert users.account["role"] == "admin"
    assert users.account["is_admin"] is True


def test_profile_image_is_normalized_to_a_square_jpeg():
    source = BytesIO()
    Image.new("RGB", (800, 400), color="purple").save(source, format="PNG")

    normalized = streamlit_app.normalize_profile_image(source.getvalue())

    with Image.open(BytesIO(normalized)) as image:
        assert image.format == "JPEG"
        assert image.size == streamlit_app.PROFILE_IMAGE_SIZE


def test_profile_image_rejects_oversized_and_invalid_uploads():
    with pytest.raises(ValueError, match="2 MB or smaller"):
        streamlit_app.normalize_profile_image(
            b"x" * (streamlit_app.MAX_PROFILE_IMAGE_BYTES + 1)
        )
    with pytest.raises(ValueError, match="valid image"):
        streamlit_app.normalize_profile_image(b"not an image")


def test_profile_image_is_persisted_for_existing_account(monkeypatch):
    account = {"_id": "profile-account", "username": "ayush"}
    users = _FakeUsers(account)
    monkeypatch.setattr(streamlit_app, "get_users_collection", lambda: users)
    source = BytesIO()
    Image.new("RGB", (64, 64), color="blue").save(source, format="PNG")

    saved_image = streamlit_app.save_profile_image("AYUSH", source.getvalue())

    assert users.account["profile_image"] == saved_image
    assert users.account["profile_image_content_type"] == "image/jpeg"


def test_persistent_session_stores_only_token_hash_and_restores_active_user(monkeypatch):
    user_id = ObjectId()
    users = _FakeUsers(
        {
            "_id": user_id,
            "username": "ayush",
            "full_name": "Ayush",
            "is_active": True,
            "role": "user",
            "is_admin": False,
            "profile_image": b"jpeg",
        }
    )
    sessions = _FakeSessions()
    monkeypatch.setattr(streamlit_app, "get_users_collection", lambda: users)
    monkeypatch.setattr(streamlit_app, "get_auth_sessions_collection", lambda: sessions)

    token = streamlit_app.create_persistent_auth_session(str(user_id))

    assert token not in sessions.records
    session_record = next(iter(sessions.records.values()))
    assert session_record["user_id"] == user_id
    assert session_record["expires_at"] > datetime.now(timezone.utc)
    assert streamlit_app.user_from_persistent_auth_session(token) == {
        "user_id": str(user_id),
        "username": "ayush",
        "name": "Ayush",
        "role": "user",
        "is_admin": False,
        "profile_image": b"jpeg",
    }

    streamlit_app.revoke_persistent_auth_session(token)
    assert streamlit_app.user_from_persistent_auth_session(token) is None


def test_persistent_session_rejects_expired_or_inactive_accounts(monkeypatch):
    user_id = ObjectId()
    users = _FakeUsers(
        {
            "_id": user_id,
            "username": "inactive",
            "is_active": False,
        }
    )
    sessions = _FakeSessions()
    token_hash = "expired-session"
    sessions.records[token_hash] = {
        "_id": token_hash,
        "user_id": user_id,
        "expires_at": datetime.now(timezone.utc) - timedelta(seconds=1),
    }
    monkeypatch.setattr(streamlit_app, "get_users_collection", lambda: users)
    monkeypatch.setattr(streamlit_app, "get_auth_sessions_collection", lambda: sessions)

    assert streamlit_app.user_from_persistent_auth_session("expired-token") is None

    token = streamlit_app.create_persistent_auth_session(str(user_id))
    assert streamlit_app.user_from_persistent_auth_session(token) is None


@pytest.mark.parametrize(
    ("password", "token", "expected"),
    [
        ("wrong-password", "a-private-administrator-bootstrap-token-123456", "current password"),
        ("current-password", "wrong-token", "setup token"),
    ],
)
def test_admin_promotion_requires_password_and_bootstrap_token(
    monkeypatch,
    password,
    token,
    expected,
):
    configured_token = "a-private-administrator-bootstrap-token-123456"
    users = _FakeUsers(
        {
            "_id": "existing-admin",
            "username": "admin",
            "hashed_password": streamlit_app.PASSWORD_HASHER.hash("current-password"),
            "is_active": True,
            "role": "user",
            "is_admin": False,
        }
    )
    monkeypatch.setattr(streamlit_app, "get_users_collection", lambda: users)
    monkeypatch.setattr(streamlit_app, "ADMIN_BOOTSTRAP_TOKEN", configured_token)
    monkeypatch.setattr(streamlit_app, "ADMIN_USERNAME", "admin")

    with pytest.raises(ValueError, match=expected):
        streamlit_app.promote_account_to_admin("admin", password, token)
    assert users.account["is_admin"] is False
