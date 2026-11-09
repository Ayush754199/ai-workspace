"""MongoDB-backed chat room operations with membership checks."""

from __future__ import annotations

import secrets
import string
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse


_INVITE_ALPHABET = string.ascii_uppercase + string.digits
_MESSAGE_LIMIT = 200


def normalize_invite_code(value: str) -> str:
    """Accept an invite code or a URL containing its `room` query parameter."""
    candidate = value.strip()
    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc or "?" in candidate:
        candidate = parse_qs(parsed.query).get("room", [""])[0]
    return candidate.strip().upper()


def create_room(
    collection: Any,
    *,
    name: str,
    owner_id: str,
    owner_username: str,
) -> dict[str, Any]:
    cleaned_name = name.strip()
    if not cleaned_name or len(cleaned_name) > 80:
        raise ValueError("Room names must contain 1–80 characters.")
    if not owner_id or not owner_username:
        raise ValueError("A signed-in account is required to create a room.")

    room = {
        "room_id": uuid.uuid4().hex,
        "name": cleaned_name,
        "invite_code": "".join(secrets.choice(_INVITE_ALPHABET) for _ in range(12)),
        "owner_id": owner_id,
        "owner_username": owner_username,
        "members": [{"user_id": owner_id, "username": owner_username}],
        "messages": [],
        "is_active": True,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    collection.insert_one(room)
    return room


def list_member_rooms(collection: Any, *, user_id: str) -> list[dict[str, Any]]:
    if not user_id:
        return []
    return list(
        collection.find(
            {
                "is_active": True,
                "$or": [{"owner_id": user_id}, {"members.user_id": user_id}],
            },
            {"messages": 0, "invite_code": 0},
        ).sort("updated_at", -1).limit(100)
    )


def join_room(
    collection: Any,
    *,
    invite: str,
    user_id: str,
    username: str,
) -> dict[str, Any]:
    code = normalize_invite_code(invite)
    if not code:
        raise ValueError("Enter a room invite code or link.")
    if not user_id or not username:
        raise ValueError("A signed-in account is required to join a room.")

    room = collection.find_one({"invite_code": code, "is_active": True})
    if room is None:
        raise LookupError("That invite code is invalid or the room is no longer active.")
    if any(member.get("user_id") == user_id for member in room.get("members", [])):
        return room

    collection.update_one(
        {
            "room_id": room["room_id"],
            "is_active": True,
            "members.user_id": {"$ne": user_id},
        },
        {
            "$addToSet": {"members": {"user_id": user_id, "username": username}},
            "$set": {"updated_at": datetime.now(timezone.utc)},
        },
    )
    updated = collection.find_one({"room_id": room["room_id"], "is_active": True})
    if updated is None or not any(
        member.get("user_id") == user_id for member in updated.get("members", [])
    ):
        raise RuntimeError("The room membership could not be confirmed. Please retry.")
    return updated


def get_member_room(collection: Any, *, room_id: str, user_id: str) -> dict[str, Any]:
    if not room_id or not user_id:
        raise LookupError("Select a room you belong to.")
    room = collection.find_one(
        {
            "room_id": room_id,
            "is_active": True,
            "$or": [{"owner_id": user_id}, {"members.user_id": user_id}],
        }
    )
    if room is None:
        raise PermissionError("This room is unavailable or your account is not a member.")
    return room


def remove_room_member(
    collection: Any,
    *,
    room_id: str,
    owner_id: str,
    member_id: str,
) -> None:
    if not room_id or not owner_id or not member_id:
        raise ValueError("Select a valid room member.")
    room = collection.find_one(
        {"room_id": room_id, "owner_id": owner_id, "is_active": True}
    )
    if room is None:
        raise PermissionError("Only the room owner can manage members.")
    if member_id == owner_id:
        raise ValueError("The room owner cannot be removed from their own room.")
    result = collection.update_one(
        {"room_id": room_id, "owner_id": owner_id, "is_active": True},
        {
            "$pull": {"members": {"user_id": member_id}},
            "$set": {"updated_at": datetime.now(timezone.utc)},
        },
    )
    if not result.matched_count:
        raise RuntimeError("The room changed while updating membership. Please retry.")


def post_room_message(
    collection: Any,
    *,
    room_id: str,
    user_id: str,
    username: str,
    content: str,
) -> dict[str, Any]:
    message_text = content.strip()
    if not message_text or len(message_text) > 4000:
        raise ValueError("Messages must contain 1–4,000 characters.")
    get_member_room(collection, room_id=room_id, user_id=user_id)
    message = {
        "message_id": uuid.uuid4().hex,
        "user_id": user_id,
        "username": username,
        "content": message_text,
        "created_at": datetime.now(timezone.utc),
    }
    result = collection.update_one(
        {
            "room_id": room_id,
            "is_active": True,
            "$or": [{"owner_id": user_id}, {"members.user_id": user_id}],
        },
        {
            "$push": {"messages": {"$each": [message], "$slice": -_MESSAGE_LIMIT}},
            "$set": {"updated_at": datetime.now(timezone.utc)},
        },
    )
    if not result.matched_count:
        raise PermissionError("Your room membership changed before the message was sent.")
    return message
