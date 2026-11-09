"""Admin-controlled feature toggles.

An admin can disable a whole capability; disabled features answer 503 with a
clear reason instead of silently failing. Toggles live in ``admin_settings``.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.models.gateway import AdminSetting


def feature_enabled(db: Session, key: str, default: bool = True) -> bool:
    setting = db.query(AdminSetting).filter(AdminSetting.key == "feature_toggles").first()
    if setting is None or not isinstance(setting.value, dict):
        return default
    return bool(setting.value.get(key, default))


def require_feature(key: str):
    """FastAPI dependency factory."""
    from app.core.database import get_db

    def check(db: Session = Depends(get_db)) -> None:
        if not feature_enabled(db, key):
            raise HTTPException(status_code=503, detail=f"The {key} feature is disabled by an administrator")

    return check


def load_gateway_order(db: Session) -> list[str] | None:
    setting = db.query(AdminSetting).filter(AdminSetting.key == "gateway_order").first()
    if setting and isinstance(setting.value, dict):
        order = setting.value.get("order")
        if isinstance(order, list):
            return [str(name) for name in order]
    return None
