"""Transactional quota reservation and usage accounting.

All counters are maintained server-side. A request id is the idempotency boundary;
provider responses are never considered frontend-provided usage data.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from enum import StrEnum

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.gateway import CapabilityQuota, ChatQuota, QuotaReservation, UsageLog, UserQuota
from app.models.user import User


class ReservationState(StrEnum):
    RESERVED = "RESERVED"
    COMMITTED = "COMMITTED"
    RELEASED = "RELEASED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class QuotaError(RuntimeError):
    pass


class QuotaExhausted(QuotaError):
    pass


class DuplicateRequest(QuotaError):
    pass


class ReservationNotFound(QuotaError):
    pass


class UncertainProviderOutcome(QuotaError):
    pass


# The quota day boundary is IST (Asia/Kolkata) — the owner's confirmed policy —
# so a daily counter resets at local midnight, not 18:30 UTC.
try:
    from zoneinfo import ZoneInfo

    CLOCK_TIMEZONE = ZoneInfo("Asia/Kolkata")
except Exception:  # pragma: no cover - fallback keeps the import safe
    from datetime import timedelta

    CLOCK_TIMEZONE = timezone(timedelta(hours=5, minutes=30), "IST")


def _today():
    # Single indirection so tests can drive the daily window with a fake clock.
    return datetime.now(CLOCK_TIMEZONE).date()


def _ensure_user_quota(db: Session, user_id: int) -> UserQuota:
    quota = db.query(UserQuota).filter(UserQuota.user_id == user_id).with_for_update().first()
    if quota:
        return quota
    quota = UserQuota(
        user_id=user_id,
        daily_limit=settings.AI_DAILY_QUOTA,
        per_chat_limit=settings.AI_PER_CHAT_QUOTA,
        daily_window=_today(),
    )
    try:
        with db.begin_nested():
            db.add(quota)
            db.flush()
    except IntegrityError:
        quota = db.query(UserQuota).filter(UserQuota.user_id == user_id).with_for_update().one()
    return quota


def _ensure_chat_quota(db: Session, user_id: int, chat_id: str) -> ChatQuota:
    quota = (
        db.query(ChatQuota)
        .filter(ChatQuota.user_id == user_id, ChatQuota.chat_id == chat_id)
        .with_for_update()
        .first()
    )
    if quota:
        return quota
    quota = ChatQuota(user_id=user_id, chat_id=chat_id)
    try:
        with db.begin_nested():
            db.add(quota)
            db.flush()
    except IntegrityError:
        quota = (
            db.query(ChatQuota)
            .filter(ChatQuota.user_id == user_id, ChatQuota.chat_id == chat_id)
            .with_for_update()
            .one()
        )
    return quota


def _ensure_capability_quota(db: Session, user_id: int, capability: str) -> CapabilityQuota:
    quota = (
        db.query(CapabilityQuota)
        .filter(CapabilityQuota.user_id == user_id, CapabilityQuota.capability == capability)
        .with_for_update()
        .first()
    )
    if quota:
        return quota
    default_limit = {
        "image": settings.AI_IMAGE_DAILY_QUOTA,
        "video": settings.AI_VIDEO_DAILY_QUOTA,
        "search": settings.AI_SEARCH_DAILY_QUOTA,
        "extraction": settings.AI_EXTRACTION_DAILY_QUOTA,
        "research": settings.AI_RESEARCH_DAILY_QUOTA,
    }.get(capability, 0)
    quota = CapabilityQuota(user_id=user_id, capability=capability, daily_limit=default_limit)
    try:
        with db.begin_nested():
            db.add(quota)
            db.flush()
    except IntegrityError:
        quota = (
            db.query(CapabilityQuota)
            .filter(CapabilityQuota.user_id == user_id, CapabilityQuota.capability == capability)
            .with_for_update()
            .one()
        )
    return quota


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite hands back naive UTC datetimes; compare them as UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class QuotaService:
    def __init__(self, *, reservation_timeout_seconds: int | None = None) -> None:
        self.reservation_timeout_seconds = reservation_timeout_seconds or settings.AI_RESERVATION_TIMEOUT_SECONDS

    def sweep_expired(self, db: Session, *, user_id: int | None = None) -> int:
        """Release reservations that expired without being committed.

        A stream that is abandoned (client disconnect, worker killed, provider hang
        past the timeout) leaves a ``RESERVED`` row whose units are still counted
        against ``reserved`` in the quota table. Until Phase 1 nothing ever brought
        those units back within the same day, so the daily quota could look
        permanently exhausted.

        Only reservations whose ``expires_at`` has passed are released. A provider
        generation normally finishes well inside ``AI_RESERVATION_TIMEOUT_SECONDS``.
        A genuinely uncertain (timed-out) request remains marked ``UNKNOWN`` in the
        ledger when its lease expires; its reserved counter is then reclaimed so a
        disconnect cannot exhaust the whole day, but the outcome is never erased.
        """
        now = datetime.now(timezone.utc)
        query = db.query(QuotaReservation).filter(
            # A RESERVED request may have lost its client/process; an UNKNOWN one may
            # have timed out at the provider. Both hold quota counters until their
            # lease expires, then must be swept so the day remains usable.
            QuotaReservation.state.in_(
                [ReservationState.RESERVED, ReservationState.UNKNOWN]
            )
        )
        if user_id is not None:
            query = query.filter(QuotaReservation.user_id == user_id)
        released = 0
        for reservation in query.limit(500).all():
            expires = _as_utc(reservation.expires_at)
            if expires is not None and expires > now:
                continue
            # Keep UNKNOWN as an audit outcome when one was already recorded; the
            # reserved counter is still reclaimed after its lease expires.
            if reservation.state == ReservationState.RESERVED:
                reservation.state = ReservationState.UNKNOWN if reservation.uncertain else ReservationState.RELEASED
            self._release_counters(db, reservation)
            # UNKNOWN is retained as the audit outcome, so exclude its reclaimed
            # reservation from future sweeps. Otherwise every later request could
            # decrement a *new* reservation's counter by this old unit.
            reservation.expires_at = None
            released += 1
        if released:
            db.commit()
        return released

    def _release_counters(self, db: Session, reservation: QuotaReservation) -> None:
        quota_model = UserQuota if reservation.capability == "chat" else CapabilityQuota
        quota_query = db.query(quota_model).filter(quota_model.user_id == reservation.user_id)
        if reservation.capability != "chat":
            quota_query = quota_query.filter(quota_model.capability == reservation.capability)
        quota = quota_query.with_for_update().one_or_none()
        if quota is not None:
            quota.reserved = max(0, quota.reserved - reservation.units)
        if reservation.capability == "chat" and reservation.chat_id:
            chat = (
                db.query(ChatQuota)
                .filter(ChatQuota.user_id == reservation.user_id, ChatQuota.chat_id == reservation.chat_id)
                .with_for_update()
                .one_or_none()
            )
            if chat is not None:
                chat.reserved = max(0, chat.reserved - reservation.units)

    def reconcile(self, db: Session, reservation_id: str, *, consumed: bool) -> QuotaReservation:
        """Resolve one reservation whose outcome is now known.

        ``consumed=True`` commits exactly one unit (idempotent: a second call returns
        the existing usage log, and a reservation that was already released is not
        billed late). ``consumed=False`` releases the reservation. A reservation that
        already reached a terminal state is returned unchanged, so a disconnect that
        races a successful commit can never commit twice or refund a committed
        generation.
        """
        reservation = (
            db.query(QuotaReservation)
            .filter(QuotaReservation.reservation_id == reservation_id)
            .with_for_update()
            .one_or_none()
        )
        if reservation is None:
            raise ReservationNotFound(reservation_id)
        if reservation.state == ReservationState.COMMITTED:
            return reservation
        if consumed:
            if reservation.state == ReservationState.RESERVED:
                self.commit(db, reservation_id, actual_units=1)
                db.refresh(reservation)
            return reservation
        if reservation.state == ReservationState.RESERVED:
            return self.release(db, reservation_id, failure=True)
        return reservation

    def reserve(
        self,
        db: Session,
        *,
        user_id: int,
        request_id: str,
        provider: str,
        model: str,
        units: int = 1,
        chat_id: str | None = None,
        capability: str = "chat",
    ) -> QuotaReservation:
        if units < 1:
            raise ValueError("Reservation units must be positive")
        if db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first() is None:
            raise QuotaError("User is not eligible for AI usage")
        if db.query(QuotaReservation).filter(QuotaReservation.request_id == request_id).first():
            raise DuplicateRequest(f"Request {request_id} already has a quota reservation")

        # Reclaim units held by abandoned requests before deciding whether the day is
        # full. Without this a few dropped connections could exhaust a user's quota.
        self.sweep_expired(db, user_id=user_id)

        today = _today()
        capability = capability.strip().lower()
        if capability not in {"chat", "image", "video", "search", "extraction", "research"}:
            raise ValueError("Unsupported AI capability")
        quota = _ensure_user_quota(db, user_id) if capability == "chat" else _ensure_capability_quota(db, user_id, capability)
        if quota.daily_window != today:
            # Old-day reservations are retained in the ledger and made uncertain;
            # they are never silently refunded or counted against the new day.
            # The cutoff must use the SAME day policy as ``_today()`` (IST), not a
            # hard-coded UTC midnight, or same-day rows would be swept as stale.
            day_start = datetime.combine(today, datetime.min.time(), tzinfo=CLOCK_TIMEZONE).astimezone(timezone.utc)
            # SQLite keeps naive UTC timestamps and refuses aware-vs-naive binds, so
            # the cutoff is bound in the column's own convention. Both conventions
            # represent the same instant for a UTC-normalised timestamp.
            cutoff = day_start.replace(tzinfo=None)
            db.query(QuotaReservation).filter(
                QuotaReservation.user_id == user_id,
                QuotaReservation.state == ReservationState.RESERVED,
                QuotaReservation.created_at < cutoff,
            ).update({"state": ReservationState.UNKNOWN, "uncertain": True}, synchronize_session=False)
            quota.daily_used = 0
            quota.reserved = 0
            quota.daily_window = today
        quota_table = UserQuota if capability == "chat" else CapabilityQuota
        daily_update = db.execute(
            update(quota_table)
            .where(
                quota_table.id == quota.id,
                (quota_table.daily_used + quota_table.reserved + units) <= quota_table.daily_limit,
            )
            .values(reserved=quota_table.reserved + units)
        )
        if daily_update.rowcount != 1:
            db.rollback()
            raise QuotaExhausted("Daily AI quota exhausted")

        chat_quota = None
        if capability == "chat" and chat_id:
            chat_quota = _ensure_chat_quota(db, user_id, chat_id)
            if chat_quota.daily_window != today:
                chat_quota.used = 0
                chat_quota.reserved = 0
                chat_quota.daily_window = today
            chat_update = db.execute(
                update(ChatQuota)
                .where(
                    ChatQuota.id == chat_quota.id,
                    (ChatQuota.used + ChatQuota.reserved + units) <= quota.per_chat_limit,
                )
                .values(reserved=ChatQuota.reserved + units)
            )
            if chat_update.rowcount != 1:
                db.rollback()
                raise QuotaExhausted("Per-chat AI quota exhausted")

        reservation = QuotaReservation(
            reservation_id=uuid.uuid4().hex,
            request_id=request_id,
            user_id=user_id,
            chat_id=chat_id,
            capability=capability,
            provider=provider,
            model=model,
            units=units,
            state=ReservationState.RESERVED,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=self.reservation_timeout_seconds),
        )
        db.add(reservation)
        db.commit()
        db.refresh(reservation)
        return reservation

    def commit(self, db: Session, reservation_id: str, *, actual_units: int = 1, detail: dict | None = None) -> UsageLog:
        if actual_units < 1:
            raise ValueError("Actual usage units must be positive")
        reservation = db.query(QuotaReservation).filter(QuotaReservation.reservation_id == reservation_id).with_for_update().first()
        if reservation is None:
            raise ReservationNotFound(reservation_id)
        existing = db.query(UsageLog).filter(UsageLog.request_id == reservation.request_id).first()
        if reservation.state == ReservationState.COMMITTED and existing:
            return existing
        if reservation.state in {ReservationState.RELEASED, ReservationState.FAILED}:
            raise QuotaError(f"Reservation is already {reservation.state}")
        quota_model = UserQuota if reservation.capability == "chat" else CapabilityQuota
        quota_query = db.query(quota_model).filter(quota_model.user_id == reservation.user_id)
        if reservation.capability != "chat":
            quota_query = quota_query.filter(quota_model.capability == reservation.capability)
        quota = quota_query.with_for_update().one()
        quota.reserved = max(0, quota.reserved - reservation.units)
        quota.daily_used += actual_units
        if reservation.capability == "chat" and reservation.chat_id:
            chat = db.query(ChatQuota).filter(ChatQuota.user_id == reservation.user_id, ChatQuota.chat_id == reservation.chat_id).with_for_update().one()
            chat.reserved = max(0, chat.reserved - reservation.units)
            chat.used += actual_units
        reservation.state = ReservationState.COMMITTED
        reservation.uncertain = False
        usage = UsageLog(
            reservation_id=reservation.reservation_id,
            request_id=reservation.request_id,
            user_id=reservation.user_id,
            provider=reservation.provider,
            model=reservation.model,
            mode=reservation.capability,
            status="COMMITTED",
            units=actual_units,
            detail=detail or {},
        )
        db.add(usage)
        db.commit()
        db.refresh(usage)
        return usage

    def rebind(self, db: Session, reservation_id: str, *, provider: str, model: str) -> QuotaReservation:
        """Move one still-reserved request to a controlled fallback route."""
        reservation = db.query(QuotaReservation).filter(QuotaReservation.reservation_id == reservation_id).with_for_update().one_or_none()
        if reservation is None:
            raise ReservationNotFound(reservation_id)
        if reservation.state != ReservationState.RESERVED:
            raise QuotaError(f"Cannot rebind reservation in state {reservation.state}")
        reservation.provider = provider
        reservation.model = model
        db.commit()
        db.refresh(reservation)
        return reservation

    def release(self, db: Session, reservation_id: str, *, failure: bool = False) -> QuotaReservation:
        reservation = db.query(QuotaReservation).filter(QuotaReservation.reservation_id == reservation_id).with_for_update().one_or_none()
        if reservation is None:
            raise ReservationNotFound(reservation_id)
        if reservation.state != ReservationState.RESERVED:
            return reservation
        quota_model = UserQuota if reservation.capability == "chat" else CapabilityQuota
        quota_query = db.query(quota_model).filter(quota_model.user_id == reservation.user_id)
        if reservation.capability != "chat":
            quota_query = quota_query.filter(quota_model.capability == reservation.capability)
        quota = quota_query.with_for_update().one()
        quota.reserved = max(0, quota.reserved - reservation.units)
        if reservation.capability == "chat" and reservation.chat_id:
            chat = db.query(ChatQuota).filter(ChatQuota.user_id == reservation.user_id, ChatQuota.chat_id == reservation.chat_id).with_for_update().one()
            chat.reserved = max(0, chat.reserved - reservation.units)
        reservation.state = ReservationState.FAILED if failure else ReservationState.RELEASED
        db.commit()
        db.refresh(reservation)
        return reservation

    def mark_uncertain(self, db: Session, reservation_id: str) -> QuotaReservation:
        reservation = db.query(QuotaReservation).filter(QuotaReservation.reservation_id == reservation_id).with_for_update().one_or_none()
        if reservation is None:
            raise ReservationNotFound(reservation_id)
        if reservation.state == ReservationState.RESERVED:
            # The provider may already have produced (and billed) an answer, so the
            # reserved unit stays counted against the user until an operator or a
            # future reconciliation proves otherwise. Only a RELEASED/FAILED row is
            # refunded -- never this one.
            reservation.state = ReservationState.UNKNOWN
            reservation.uncertain = True
            db.commit()
            db.refresh(reservation)
        return reservation

    def expire_pending_for_user(self, db: Session, *, user_id: int) -> int:
        """Release this user's expired reservations (called before each reserve)."""
        try:
            return self.sweep_expired(db, user_id=user_id)
        except Exception:  # noqa: BLE001 - the sweep must never block a new request
            db.rollback()
            return 0

    def pending_for(self, db: Session, *, user_id: int) -> int:
        """Number of RESERVED rows for a user (diagnostics/tests)."""
        return (
            db.query(QuotaReservation)
            .filter(
                QuotaReservation.user_id == user_id,
                QuotaReservation.state == ReservationState.RESERVED,
            )
            .count()
        )

    def set_limits(self, db: Session, *, user_id: int, daily_limit: int, per_chat_limit: int) -> UserQuota:
        if daily_limit < 1 or per_chat_limit < 1:
            raise ValueError("Quota limits must be positive")
        quota = _ensure_user_quota(db, user_id)
        quota.daily_limit = daily_limit
        quota.per_chat_limit = per_chat_limit
        db.commit()
        db.refresh(quota)
        return quota

    def set_capability_limits(self, db: Session, *, user_id: int, capability: str, daily_limit: int) -> CapabilityQuota:
        capability = capability.strip().lower()
        if capability not in {"image", "video", "search", "extraction", "research"}:
            raise ValueError("Capability limits are for image, video, search, extraction, or research")
        if daily_limit < 0:
            raise ValueError("Quota limits cannot be negative")
        quota = _ensure_capability_quota(db, user_id, capability)
        quota.daily_limit = daily_limit
        db.commit()
        db.refresh(quota)
        return quota
