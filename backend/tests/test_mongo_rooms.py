from copy import deepcopy

import pytest

from backend.mongo_rooms import (
    create_room,
    get_member_room,
    join_room,
    list_member_rooms,
    normalize_invite_code,
    post_room_message,
    remove_room_member,
)


def _nested(document, path):
    value = document
    for part in path.split("."):
        if isinstance(value, list):
            return [item.get(part) for item in value if isinstance(item, dict)]
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _matches(document, query):
    for key, expected in query.items():
        if key == "$or":
            if not any(_matches(document, option) for option in expected):
                return False
            continue
        actual = _nested(document, key)
        if isinstance(expected, dict) and "$ne" in expected:
            values = actual if isinstance(actual, list) else [actual]
            if expected["$ne"] in values:
                return False
        elif isinstance(expected, dict):
            if expected != actual:
                return False
        elif isinstance(actual, list):
            if expected not in actual:
                return False
        elif actual != expected:
            return False
    return True


class FakeUpdateResult:
    def __init__(self, matched_count):
        self.matched_count = matched_count


class FakeCursor(list):
    def sort(self, key, direction):
        super().sort(key=lambda item: item.get(key) or "", reverse=direction < 0)
        return self

    def limit(self, count):
        return FakeCursor(self[:count])


class FakeCollection:
    def __init__(self):
        self.documents = []

    def insert_one(self, document):
        self.documents.append(deepcopy(document))

    def find_one(self, query, projection=None):
        for document in self.documents:
            if _matches(document, query):
                return deepcopy(document)
        return None

    def find(self, query, projection=None):
        return FakeCursor(
            deepcopy(document)
            for document in self.documents
            if _matches(document, query)
        )

    def update_one(self, query, update):
        for document in self.documents:
            if not _matches(document, query):
                continue
            for path, value in update.get("$set", {}).items():
                document[path] = value
            for path, value in update.get("$addToSet", {}).items():
                document.setdefault(path, [])
                if value not in document[path]:
                    document[path].append(value)
            for path, value in update.get("$pull", {}).items():
                document[path] = [
                    item
                    for item in document.get(path, [])
                    if not all(item.get(key) == target for key, target in value.items())
                ]
            for path, value in update.get("$push", {}).items():
                entries = document.setdefault(path, [])
                entries.extend(value["$each"])
                if "$slice" in value:
                    document[path] = entries[value["$slice"] :]
            return FakeUpdateResult(1)
        return FakeUpdateResult(0)


def test_room_lifecycle_requires_membership_and_owner_for_removal():
    rooms = FakeCollection()
    room = create_room(
        rooms,
        name="Design review",
        owner_id="owner-id",
        owner_username="owner",
    )

    joined = join_room(
        rooms,
        invite=room["invite_code"].lower(),
        user_id="member-id",
        username="member",
    )
    assert joined["room_id"] == room["room_id"]
    assert len(list_member_rooms(rooms, user_id="member-id")) == 1
    assert get_member_room(
        rooms,
        room_id=room["room_id"],
        user_id="member-id",
    )["name"] == "Design review"

    message = post_room_message(
        rooms,
        room_id=room["room_id"],
        user_id="member-id",
        username="member",
        content="Ready for review",
    )
    assert message["content"] == "Ready for review"
    assert rooms.find_one({"room_id": room["room_id"]})["messages"] == [message]

    with pytest.raises(PermissionError, match="Only the room owner"):
        remove_room_member(
            rooms,
            room_id=room["room_id"],
            owner_id="member-id",
            member_id="owner-id",
        )

    remove_room_member(
        rooms,
        room_id=room["room_id"],
        owner_id="owner-id",
        member_id="member-id",
    )
    assert list_member_rooms(rooms, user_id="member-id") == []
    with pytest.raises(PermissionError, match="not a member"):
        get_member_room(rooms, room_id=room["room_id"], user_id="member-id")


def test_invite_link_and_invalid_room_input():
    assert normalize_invite_code("https://example.test/?room=abc123") == "ABC123"
    assert normalize_invite_code(" abc123 ") == "ABC123"
    with pytest.raises(ValueError, match="1–80"):
        create_room(
            FakeCollection(),
            name=" " + "x" * 81,
            owner_id="owner-id",
            owner_username="owner",
        )
