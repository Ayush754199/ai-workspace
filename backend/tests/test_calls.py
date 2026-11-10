from datetime import datetime, timezone
import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.services import call_service


def call_document(status="ringing"):
    return {
        "call_id": "call-1", "participant_ids": [1, 2], "room_name": "room-1",
        "status": status, "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }


def test_call_transition_records_end_and_duration(monkeypatch):
    call_service.mongo.calls = AsyncMock()
    monkeypatch.setattr(call_service, "write_event", AsyncMock())
    call = call_document("accepted")
    call["accepted_at"] = datetime.now(timezone.utc)
    result = asyncio.run(call_service.transition(call, "ended", 2, reason="hangup"))
    assert result["status"] == "ended"
    assert result["terminal_reason"] == "hangup"
    call_service.mongo.calls.update_one.assert_awaited_once()


def test_terminal_call_cannot_be_reopened(monkeypatch):
    monkeypatch.setattr(call_service, "write_event", AsyncMock())
    with pytest.raises(HTTPException) as exc:
        asyncio.run(call_service.transition(call_document("ended"), "accepted", 2))
    assert exc.value.status_code == 409


def test_ringing_call_cannot_be_ended_before_acceptance(monkeypatch):
    monkeypatch.setattr(call_service, "write_event", AsyncMock())
    with pytest.raises(HTTPException) as exc:
        asyncio.run(call_service.transition(call_document("ringing"), "ended", 2))
    assert exc.value.status_code == 409


def test_cleanup_stale_calls_closes_old_ringing_and_active_records(monkeypatch):
    mongo = type("MongoStub", (), {"enabled": True, "calls": AsyncMock()})()
    monkeypatch.setattr(call_service, "mongo", mongo)
    asyncio.run(call_service.cleanup_stale_calls())
    assert mongo.calls.update_many.await_count == 2
    first = mongo.calls.update_many.await_args_list[0].args[0]
    second = mongo.calls.update_many.await_args_list[1].args[0]
    assert first["status"] == "ringing"
    assert second["status"]["$in"] == ["accepted", "active"]


def test_purge_call_data_only_targets_call_collections(monkeypatch):
    class Result:
        def __init__(self, count):
            self.deleted_count = count

    class Collection:
        async def delete_many(self, query):
            assert query == {}
            return Result(3)

    mongo = type("MongoStub", (), {
        "enabled": True,
        "calls": Collection(),
        "call_events": Collection(),
        "call_messages": Collection(),
    })()
    monkeypatch.setattr(call_service, "mongo", mongo)
    assert asyncio.run(call_service.purge_call_data()) == {
        "calls": 3, "call_events": 3, "call_messages": 3,
    }
