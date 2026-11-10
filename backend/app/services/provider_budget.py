"""Atomic provider budget accounting for Muse USD caps and Cloudflare neurons."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.gateway import ProviderBudget, ProviderBudgetReservation


class ProviderBudgetError(RuntimeError):
    pass


def _window_key(period: str) -> str:
    if period == "monthly":
        return datetime.now(timezone.utc).strftime("%Y-%m")
    try:
        zone = ZoneInfo(settings.CLOUDFLARE_RESET_TIMEZONE)
    except Exception:
        zone = timezone.utc
    return datetime.now(zone).date().isoformat()


def _limit(provider: str, bucket: str, period: str) -> tuple[float, str]:
    if provider == "muse_spark" and period == "monthly":
        return settings.MUSE_SPARK_MONTHLY_USD, "usd"
    if provider == "muse_image" and period == "monthly":
        return settings.MUSE_IMAGE_MONTHLY_USD, "usd"
    if provider == "cloudflare" and period == "daily":
        if bucket not in {"research", "image"}:
            raise ProviderBudgetError("Cloudflare bucket must be research or image")
        total = settings.CLOUDFLARE_DAILY_NEURON_LIMIT
        return total * 0.5, "neurons"
    raise ProviderBudgetError(f"Unsupported provider budget: {provider}/{bucket}/{period}")


def reserve(db: Session, *, request_id: str, provider: str, bucket: str, period: str, units: float = 1.0) -> ProviderBudgetReservation:
    if units <= 0:
        raise ValueError("Budget reservation units must be positive")
    if db.query(ProviderBudgetReservation).filter_by(request_id=request_id).first():
        raise ProviderBudgetError("Duplicate provider budget request")
    limit, unit_name = _limit(provider, bucket, period)
    if limit <= 0:
        raise ProviderBudgetError(f"{provider} {bucket} budget is not configured")
    budget = db.query(ProviderBudget).filter_by(provider=provider, bucket=bucket, period=period).with_for_update().first()
    if budget is None:
        budget = ProviderBudget(provider=provider, bucket=bucket, period=period, window_key=_window_key(period), limit_units=limit, unit_name=unit_name)
        db.add(budget)
        db.flush()
    elif budget.window_key != _window_key(period):
        budget.window_key = _window_key(period)
        budget.used_units = 0
        budget.reserved_units = 0
        budget.limit_units = limit
    else:
        budget.limit_units = limit
    result = db.execute(update(ProviderBudget).where(
        ProviderBudget.id == budget.id,
        (ProviderBudget.used_units + ProviderBudget.reserved_units + units) <= ProviderBudget.limit_units,
    ).values(reserved_units=ProviderBudget.reserved_units + units))
    if result.rowcount != 1:
        db.rollback()
        raise ProviderBudgetError(f"{provider} {bucket} budget exhausted")
    reservation = ProviderBudgetReservation(request_id=request_id, budget_id=budget.id, units=units)
    db.add(reservation)
    db.commit()
    db.refresh(reservation)
    return reservation


def commit(db: Session, reservation_id: int, *, actual_units: float | None = None, detail: dict | None = None) -> None:
    reservation = db.query(ProviderBudgetReservation).filter_by(id=reservation_id).with_for_update().first()
    if reservation is None or reservation.state != "RESERVED":
        raise ProviderBudgetError("Provider budget reservation is not active")
    units = reservation.units if actual_units is None else actual_units
    budget = db.query(ProviderBudget).filter_by(id=reservation.budget_id).with_for_update().one()
    budget.reserved_units = max(0, budget.reserved_units - reservation.units)
    budget.used_units += units
    reservation.state = "COMMITTED"
    reservation.detail = detail or {}
    db.commit()


def release(db: Session, reservation_id: int, *, detail: dict | None = None) -> None:
    reservation = db.query(ProviderBudgetReservation).filter_by(id=reservation_id).with_for_update().first()
    if reservation is None or reservation.state != "RESERVED":
        return
    budget = db.query(ProviderBudget).filter_by(id=reservation.budget_id).with_for_update().one()
    budget.reserved_units = max(0, budget.reserved_units - reservation.units)
    reservation.state = "RELEASED"
    reservation.detail = detail or {}
    db.commit()


def mark_uncertain(db: Session, reservation_id: int, *, detail: dict | None = None) -> None:
    """Keep an uncertain reservation held until an operator reconciles it.

    A timeout may occur after a provider has accepted/billed a request. Releasing
    that reservation immediately could allow the configured cap to be exceeded.
    """
    reservation = db.query(ProviderBudgetReservation).filter_by(id=reservation_id).with_for_update().first()
    if reservation is None or reservation.state != "RESERVED":
        return
    reservation.state = "UNCERTAIN"
    reservation.detail = detail or {}
    db.commit()
