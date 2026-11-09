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


class QuotaService:
    def __init__(self, *, reservation_timeout_seconds: int | None = None) -> None:
        self.reservation_timeout_seconds = reservation_timeout_seconds or settings.AI_RESERVATION_TIMEOUT_SECONDS

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
            reservation.state = ReservationState.UNKNOWN
            reservation.uncertain = True
            db.commit()
            db.refresh(reservation)
        return reservation

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
