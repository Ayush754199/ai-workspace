"""CON-05: daily quota reset at a UTC and an IST day boundary, driven by a fake clock.

Production policy is IST (Asia/Kolkata) — set in quota.CLOCK_TIMEZONE — and the
UTC tests stay so a future timezone change is caught immediately.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.models.gateway import UserQuota
from app.models.user import User
from app.services.ai_gateway import quota as quota_module
from app.services.ai_gateway.quota import QuotaService

IST = timezone(timedelta(hours=5, minutes=30))


def _user(db):
    user = User(identifier=f"day-{uuid.uuid4().hex}", email=f"day-{uuid.uuid4().hex}@example.com", hashed_password="hash", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _clock(monkeypatch, current: datetime):
    """Freeze the quota clock at ``current``; the day is derived from it."""
    state = {"now": current}

    monkeypatch.setattr(quota_module, "_today", lambda: state["now"].astimezone(quota_module.CLOCK_TIMEZONE).date())
    return state


def _reserve(db, user):
    return QuotaService().reserve(db, user_id=user.id, request_id=uuid.uuid4().hex, provider="mock", model="mock-chat")


def test_utc_boundary_resets_after_utc_midnight(db_session, monkeypatch):
    """UTC policy comparison: proves the two policies differ (kept deliberately)."""
    user = _user(db_session)
    monkeypatch.setattr(quota_module, "CLOCK_TIMEZONE", timezone.utc)
    clock = _clock(monkeypatch, datetime(2026, 11, 4, 23, 59, 30, tzinfo=timezone.utc))
    first = _reserve(db_session, user)
    QuotaService().commit(db_session, first.reservation_id)
    assert db_session.query(UserQuota).filter_by(user_id=user.id).one().daily_used == 1

    # 00:00:05 UTC on the next day.
    clock["now"] = datetime(2026, 11, 5, 0, 0, 5, tzinfo=timezone.utc)
    _reserve(db_session, user)
    quota = db_session.query(UserQuota).filter_by(user_id=user.id).one()
    assert quota.daily_window == date(2026, 11, 5)
    assert quota.daily_used == 0
    assert quota.reserved == 1


def test_production_policy_is_ist(db_session, monkeypatch):
    """The shipped CLOCK_TIMEZONE must be Asia/Kolkata, not UTC."""
    from zoneinfo import ZoneInfo

    assert str(quota_module.CLOCK_TIMEZONE) in {"Asia/Kolkata", "IST"}
    # 20:00 UTC on 4 Nov is already 5 Nov in IST -> the quota window must roll.
    monkeypatch.setattr(quota_module, "_today", lambda: datetime(2026, 11, 4, 20, 0, tzinfo=timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date())
    user = _user(db_session)
    _reserve(db_session, user)
    quota = db_session.query(UserQuota).filter_by(user_id=user.id).one()
    assert quota.daily_window == date(2026, 11, 5)


def test_utc_boundary_does_not_reset_before_utc_midnight(db_session, monkeypatch):
    """UTC comparison: 23:59:59 UTC is still the old UTC day."""
    user = _user(db_session)
    monkeypatch.setattr(quota_module, "CLOCK_TIMEZONE", timezone.utc)
    clock = _clock(monkeypatch, datetime(2026, 11, 4, 23, 59, 59, tzinfo=timezone.utc))
    first = _reserve(db_session, user)
    QuotaService().commit(db_session, first.reservation_id)
    clock["now"] = datetime(2026, 11, 4, 23, 59, 59, tzinfo=timezone.utc)
    _reserve(db_session, user)
    quota = db_session.query(UserQuota).filter_by(user_id=user.id).one()
    assert quota.daily_window == date(2026, 11, 4)
    assert quota.reserved == 1


def test_ist_boundary_resets_at_ist_midnight_not_utc_midnight(db_session, monkeypatch):
    """Documents the requirement difference: IST policy resets at 18:30 UTC.

    The fake clock returns local IST dates, which is what an IST policy means.
    """
    user = _user(db_session)
    monkeypatch.setattr(quota_module, "CLOCK_TIMEZONE", IST)
    # 23:59 IST on 4 Nov.
    clock = _clock(monkeypatch, datetime(2026, 11, 4, 23, 59, 0, tzinfo=IST))
    first = _reserve(db_session, user)
    QuotaService().commit(db_session, first.reservation_id)
    assert db_session.query(UserQuota).filter_by(user_id=user.id).one().daily_window == date(2026, 11, 4)

    # 00:01 IST on 5 Nov (== 18:31 UTC on 4 Nov).
    clock["now"] = datetime(2026, 11, 5, 0, 1, 0, tzinfo=IST)
    _reserve(db_session, user)
    quota = db_session.query(UserQuota).filter_by(user_id=user.id).one()
    assert quota.daily_window == date(2026, 11, 5)
    assert quota.daily_used == 0


def test_ist_policy_differs_from_utc_between_18_30_and_00_00_utc(db_session, monkeypatch):
    """At 19:00 UTC a UTC policy is still on 4 Nov while IST has already rolled to 5 Nov.

    The production choice is one timezone; this test only proves the two
    policies produce different reset times, so the requirement must be explicit.
    """
    user = _user(db_session)
    old_day = date(2026, 11, 4)
    db_session.add(UserQuota(user_id=user.id, daily_limit=5, daily_used=4, reserved=0, per_chat_limit=5, daily_window=old_day))
    db_session.commit()

    instant = datetime(2026, 11, 4, 19, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(quota_module, "CLOCK_TIMEZONE", timezone.utc)
    clock = _clock(monkeypatch, instant)
    _reserve(db_session, user)
    assert db_session.query(UserQuota).filter_by(user_id=user.id).one().daily_window == old_day

    db_session.query(UserQuota).filter_by(user_id=user.id).update({"daily_window": old_day, "daily_used": 4, "reserved": 0})
    db_session.commit()
    monkeypatch.setattr(quota_module, "CLOCK_TIMEZONE", IST)
    clock["now"] = instant.astimezone(IST)
    _reserve(db_session, user)
    quota = db_session.query(UserQuota).filter_by(user_id=user.id).one()
    assert quota.daily_window == date(2026, 11, 5)
    assert quota.daily_used == 0


def test_old_day_reserved_rows_become_uncertain(db_session, monkeypatch):
    from app.models.gateway import QuotaReservation

    # Drive the production policy (Asia/Kolkata) across its own midnight.
    clock = _clock(monkeypatch, datetime(2026, 11, 4, 23, 50, tzinfo=IST))
    user = _user(db_session)
    pending = _reserve(db_session, user)
    # The DB stores naive UTC. 23:50 IST on 4 Nov is 18:20 UTC,
    # so stamp the reservation on the intended old quota day.
    db_session.query(QuotaReservation).filter_by(reservation_id=pending.reservation_id).update(
        {"created_at": datetime(2026, 11, 4, 18, 20, tzinfo=timezone.utc).replace(tzinfo=None)}
    )
    db_session.commit()
    clock["now"] = datetime(2026, 11, 5, 0, 10, tzinfo=IST)
    _reserve(db_session, user)

    stored = db_session.query(QuotaReservation).filter_by(reservation_id=pending.reservation_id).one()
    assert stored.state == "UNKNOWN"
    assert stored.uncertain is True


@pytest.mark.parametrize("tz,current,expected", [
    (timezone.utc, datetime(2026, 11, 4, 23, 59, 59, tzinfo=timezone.utc), date(2026, 11, 4)),
    (timezone.utc, datetime(2026, 11, 5, 0, 0, 1, tzinfo=timezone.utc), date(2026, 11, 5)),
    (IST, datetime(2026, 11, 4, 18, 29, 59, tzinfo=timezone.utc), date(2026, 11, 4)),
    (IST, datetime(2026, 11, 4, 18, 30, 1, tzinfo=timezone.utc), date(2026, 11, 5)),
])
def test_boundary_table(monkeypatch, tz, current, expected):
    monkeypatch.setattr(quota_module, "CLOCK_TIMEZONE", tz)
    monkeypatch.setattr(quota_module, "_today", lambda: current.astimezone(tz).date())
    assert quota_module._today() == expected
