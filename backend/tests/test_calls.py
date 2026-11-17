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


def test_stale_sweeper_runs_without_any_client_traffic(monkeypatch):
    """Abandoned calls must expire even if nobody opens history/busy again."""
    mongo = type("MongoStub", (), {"enabled": True, "calls": AsyncMock()})()
    monkeypatch.setattr(call_service, "mongo", mongo)
    ticks = {"count": 0}

    async def fake_sleep(_delay):
        ticks["count"] += 1
        if ticks["count"] >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(call_service.asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(call_service.sweep_stale_calls_forever(interval_seconds=1))
    # Two ticks, each closing ringing + accepted/active records.
    assert mongo.calls.update_many.await_count == 4


def test_stale_sweeper_survives_a_failing_tick(monkeypatch):
    """One Mongo hiccup must not stop the maintenance loop."""
    mongo = type("MongoStub", (), {"enabled": True, "calls": AsyncMock()})()
    mongo.calls.update_many.side_effect = [RuntimeError("mongo down"), AsyncMock(), AsyncMock()]
    monkeypatch.setattr(call_service, "mongo", mongo)
    ticks = {"count": 0}

    async def fake_sleep(_delay):
        ticks["count"] += 1
        if ticks["count"] >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(call_service.asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(call_service.sweep_stale_calls_forever(interval_seconds=1))
    # The failing tick stopped before its second update, then the loop recovered.
    assert mongo.calls.update_many.await_count == 3
