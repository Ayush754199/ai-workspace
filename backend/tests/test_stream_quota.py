"""Phase 1D: SSE quota reservations are always settled exactly once.

The stream endpoint used to reserve inside the generator and only commit at the
end. A client disconnect (GeneratorExit) or a cancelled task skipped both commit
and release, so the reserved unit stayed counted forever and the daily quota could
be exhausted by abandoned requests.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.gateway import ChatQuota, QuotaReservation, UsageLog, UserQuota
from app.models.user import User
from app.services.ai_gateway.gateway import AIGateway
from app.services.ai_gateway.providers import GatewayProviderError, GatewayResponse
from app.services.ai_gateway.quota import (
    QuotaExhausted,
    QuotaService,
    ReservationState,
)


def _user(db) -> User:
    user = User(
        identifier=f"stream-{uuid.uuid4().hex}",
        email=f"stream-{uuid.uuid4().hex}@example.com",
        hashed_password="hash",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _reserve(db, user, *, capability="chat", chat_id="ai-conversation-1", request_id=None):
    return QuotaService().reserve(
        db,
        user_id=user.id,
        request_id=request_id or uuid.uuid4().hex,
        provider="mock",
        model="mock-chat",
        capability=capability,
        chat_id=chat_id,
    )


def _counters(db, user, capability="chat"):
    if capability == "chat":
        quota = db.query(UserQuota).filter_by(user_id=user.id).one()
        return quota.daily_used, quota.reserved
    raise AssertionError("capability counters are checked through UserQuota here")


def test_successful_stream_commits_exactly_one_unit(db_session):
    user = _user(db_session)
    reservation = _reserve(db_session, user)
    service = QuotaService()
    assert service.reconcile(db_session, reservation.reservation_id, consumed=True).state == ReservationState.COMMITTED
    # A late duplicate (e.g. the finally-block racing the success path) is a no-op.
    service.reconcile(db_session, reservation.reservation_id, consumed=True)
    assert _counters(db_session, user) == (1, 0)
    assert db_session.query(UsageLog).filter_by(reservation_id=reservation.reservation_id).count() == 1


def test_provider_failure_releases_the_reservation(db_session):
    user = _user(db_session)
    reservation = _reserve(db_session, user)
    QuotaService().reconcile(db_session, reservation.reservation_id, consumed=False)
    assert _counters(db_session, user) == (0, 0)
    stored = db_session.query(QuotaReservation).filter_by(reservation_id=reservation.reservation_id).one()
    assert stored.state == ReservationState.FAILED
    assert stored.uncertain is False


def test_client_disconnect_releases_the_reservation(db_session):
    """Simulates the generator's finally-block after GeneratorExit."""
    user = _user(db_session)
    reservation = _reserve(db_session, user)
    consumed = False
    with pytest.raises(GeneratorExit):
        try:
            raise GeneratorExit  # the client went away mid-stream
        finally:
            if not consumed:
                QuotaService().reconcile(db_session, reservation.reservation_id, consumed=False)
    assert _counters(db_session, user) == (0, 0)
    stored = db_session.query(QuotaReservation).filter_by(reservation_id=reservation.reservation_id).one()
    assert stored.state == ReservationState.FAILED


def test_timeout_is_marked_uncertain_and_not_refunded_immediately(db_session):
    user = _user(db_session)
    reservation = _reserve(db_session, user)
    QuotaService().mark_uncertain(db_session, reservation.reservation_id)
    # Uncertain stays counted until the lease expires; it is never silently refunded.
    assert _counters(db_session, user) == (0, 1)
    stored = db_session.query(QuotaReservation).filter_by(reservation_id=reservation.reservation_id).one()
    assert stored.state == ReservationState.UNKNOWN and stored.uncertain is True


def test_expired_uncertain_reservation_is_eventually_freed(db_session):
    user = _user(db_session)
    reservation = _reserve(db_session, user)
    QuotaService().mark_uncertain(db_session, reservation.reservation_id)
    # Force the lease into the past, then read the row again (bulk update bypasses
    # the identity map, so without expire_all the old expires_at would be used).
    db_session.query(QuotaReservation).filter_by(reservation_id=reservation.reservation_id).update(
        {"expires_at": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=5)}
    )
    db_session.commit()
    db_session.expire_all()
    assert QuotaService().sweep_expired(db_session, user_id=user.id) == 1
    assert _counters(db_session, user) == (0, 0)
    stored = db_session.query(QuotaReservation).filter_by(reservation_id=reservation.reservation_id).one()
    # The outcome stays recorded as uncertain for auditing; only the counter is freed.
    assert stored.state == ReservationState.UNKNOWN
    assert stored.expires_at is None
    # A later reservation must not sweep and decrement the already-reclaimed unit
    # a second time.
    fresh = _reserve(db_session, user)
    assert fresh.state == ReservationState.RESERVED
    assert _counters(db_session, user) == (0, 1)


def test_abandoned_reservation_cannot_exhaust_the_day(db_session):
    user = _user(db_session)
    service = QuotaService()
    service.set_limits(db_session, user_id=user.id, daily_limit=1, per_chat_limit=1)
    abandoned = _reserve(db_session, user)
    # The process died: the row is still RESERVED and its lease runs out.
    db_session.query(QuotaReservation).filter_by(reservation_id=abandoned.reservation_id).update(
        {"expires_at": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)}
    )
    db_session.commit()
    db_session.expire_all()
    # The next request must still be allowed.
    fresh = _reserve(db_session, user)
    assert fresh.state == ReservationState.RESERVED
    assert db_session.query(QuotaReservation).filter_by(reservation_id=abandoned.reservation_id).one().state == ReservationState.RELEASED


def test_real_usage_still_exhausts_the_quota(db_session):
    """Enforcement is never weakened to satisfy the reconciliation tests."""
    user = _user(db_session)
    service = QuotaService()
    service.set_limits(db_session, user_id=user.id, daily_limit=1, per_chat_limit=1)
    first = _reserve(db_session, user)
    service.reconcile(db_session, first.reservation_id, consumed=True)
    with pytest.raises(QuotaExhausted):
        _reserve(db_session, user, chat_id="ai-conversation-2")
    # Consumed usage is not resurrected by the expiry sweep either.
    assert service.sweep_expired(db_session, user_id=user.id) == 0
    assert _counters(db_session, user) == (1, 0)


def test_research_and_chat_share_the_same_lifecycle(db_session):
    user = _user(db_session)
    service = QuotaService()
    research = _reserve(db_session, user, capability="research", chat_id=None)
    service.reconcile(db_session, research.reservation_id, consumed=True)
    chat = _reserve(db_session, user)
    service.reconcile(db_session, chat.reservation_id, consumed=False)
    assert db_session.query(QuotaReservation).filter_by(reservation_id=research.reservation_id).one().state == ReservationState.COMMITTED
    assert db_session.query(QuotaReservation).filter_by(reservation_id=chat.reservation_id).one().state == ReservationState.FAILED
    assert service.pending_for(db_session, user_id=user.id) == 0


def test_retry_after_failure_reuses_no_billing(db_session, monkeypatch):
    """A retry is a NEW request: the failed one is released, the retry bills once."""
    from app.core.config import settings

    user = _user(db_session)
    monkeypatch.setattr(settings, "AI_GATEWAY_MODE", "live")
    monkeypatch.setattr("app.services.ai_gateway.gateway.get_provider_credentials", lambda name: ("k",))
    monkeypatch.setattr(settings, "OPENROUTER_KEY_1", "configured-for-test")

    gateway = AIGateway()
    attempts = {"n": 0}

    def fake_provider(route):
        class Adapter:
            def generate(self, prompt, *, system_prompt=None, model=None):
                attempts["n"] += 1
                if attempts["n"] == 1:
                    raise GatewayProviderError("provider down", status_code=503, retryable=False)
                return GatewayResponse("ok", route.provider, route.model)

        return Adapter()

    monkeypatch.setattr(gateway, "provider", fake_provider)
    # Pin the routing order so the fake provider is deterministic regardless of the
    # allow-list order or a leftover admin setting from another test.
    gateway.set_order(["openrouter", "gemini"])
    service = QuotaService()
    first_id = f"retry-{uuid.uuid4().hex}"
    with pytest.raises(GatewayProviderError):
        gateway.generate_with_quota(db_session, user_id=user.id, request_id=first_id, prompt="hi")
    db_session.expire_all()
    failed = db_session.query(QuotaReservation).filter_by(request_id=first_id).one()
    assert failed.state in {ReservationState.RELEASED, ReservationState.FAILED}
    assert _counters(db_session, user) == (0, 0)

    second_id = f"retry-{uuid.uuid4().hex}"
    answer, _route = gateway.generate_with_quota(db_session, user_id=user.id, request_id=second_id, prompt="hi")
    assert answer == "ok"
    assert _counters(db_session, user) == (1, 0)
    assert db_session.query(UsageLog).filter_by(request_id=second_id).count() == 1
    assert service.pending_for(db_session, user_id=user.id) == 0


def test_chat_quota_counter_is_released_with_the_reservation(db_session):
    user = _user(db_session)
    reservation = _reserve(db_session, user)
    quota = db_session.query(ChatQuota).filter_by(user_id=user.id, chat_id="ai-conversation-1").one()
    assert quota.reserved == 1
    QuotaService().reconcile(db_session, reservation.reservation_id, consumed=False)
    db_session.expire_all()
    quota = db_session.query(ChatQuota).filter_by(user_id=user.id, chat_id="ai-conversation-1").one()
    assert quota.reserved == 0 and quota.used == 0
