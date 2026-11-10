"""Administrator-only user and usage management endpoints."""

import csv
import io
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from datetime import datetime
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin
from app.core.config import settings
from app.core.database import get_db
from app.models.user import User, UserFeaturePermission
from app.models.room import AdminAuditLog, Message, Room, RoomInvite
from app.models.gateway import CapabilityQuota, Provider, ProviderBudget, ProviderModel, UserQuota
from app.services.ai_gateway.quota import QuotaService
from app.services.ai_gateway.openrouter_catalog import discover_free_models
from app.services.ai_gateway import GatewayUnavailableError
from backend.key_rotator import get_provider_credentials

router = APIRouter(prefix="/admin", tags=["admin"])


class UserStatusUpdate(BaseModel):
    is_active: bool | None = None
    is_admin: bool | None = None
    role: str | None = Field(default=None, pattern="^(user|admin|doctor|ca|engineer)$")


class QuotaUpdate(BaseModel):
    daily_limit: int
    per_chat_limit: int


class CapabilityQuotaUpdate(BaseModel):
    capability: str = Field(pattern="^(image|video|search|extraction|research)$")
    daily_limit: int = Field(ge=0)



class ProviderModelUpdate(BaseModel):
    provider: str
    provider_enabled: bool = False
    model: str
    enabled: bool = False
    is_free: bool = False
    capabilities: list[str] = Field(default_factory=list)
    availability: str = "unknown"
    verified_at: datetime | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    restrictions: list[str] = Field(default_factory=list)


def serialize_user(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "is_active": user.is_active,
        "is_admin": user.is_admin,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


@router.get("/users")
def list_users(_: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    return [serialize_user(user) for user in db.query(User).order_by(User.created_at.desc()).all()]


@router.get("/analytics")
def get_analytics(_: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    total_users = db.query(User).count()
    active_users = db.query(User).filter(User.is_active.is_(True)).count()
    return {
        "total_users": total_users,
        "active_chats": 0,
        "documents_analyzed": 0,
        "excel_exports_generated": 0,
        "active_users": active_users,
    }


@router.patch("/users/{user_id}/status")
def update_user_status(
    user_id: int,
    update: UserStatusUpdate,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == current_admin.id and update.is_active is False:
        raise HTTPException(status_code=400, detail="You cannot disable your own account")
    for field, value in update.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    db.commit()
    db.refresh(user)
    return serialize_user(user)


@router.get("/users/{user_id}/quota")
def get_user_quota(user_id: int, _: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    quota = db.query(UserQuota).filter(UserQuota.user_id == user_id).first()
    if quota is None:
        raise HTTPException(status_code=404, detail="Quota configuration not found")
    return {
        "user_id": user_id,
        "daily_limit": quota.daily_limit,
        "daily_used": quota.daily_used,
        "per_chat_limit": quota.per_chat_limit,
        "reserved": quota.reserved,
        "daily_window": quota.daily_window.isoformat() if quota.daily_window else None,
    }


@router.put("/users/{user_id}/quota")
def set_user_quota(
    user_id: int,
    update: QuotaUpdate,
    _: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    if db.query(User).filter(User.id == user_id).first() is None:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        quota = QuotaService().set_limits(
            db,
            user_id=user_id,
            daily_limit=update.daily_limit,
            per_chat_limit=update.per_chat_limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "user_id": user_id,
        "daily_limit": quota.daily_limit,
        "per_chat_limit": quota.per_chat_limit,
    }


@router.get("/users/{user_id}/quotas")
def get_user_quotas(user_id: int, _: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    chat = db.query(UserQuota).filter(UserQuota.user_id == user_id).first()
    media = db.query(CapabilityQuota).filter(CapabilityQuota.user_id == user_id).all()
    if chat is None:
        raise HTTPException(status_code=404, detail="Quota configuration not found")
    result = {
        "chat": {"daily_limit": chat.daily_limit, "daily_used": chat.daily_used, "per_chat_limit": chat.per_chat_limit, "reserved": chat.reserved},
        "image": {"daily_limit": 0, "daily_used": 0, "reserved": 0},
        "video": {"daily_limit": 0, "daily_used": 0, "reserved": 0},
        "search": {"daily_limit": 0, "daily_used": 0, "reserved": 0},
        "extraction": {"daily_limit": 0, "daily_used": 0, "reserved": 0},
        "research": {"daily_limit": 0, "daily_used": 0, "reserved": 0},
    }
    from app.services.ai_gateway.quota import _today
    daily_window = _today().isoformat()
    defaults = {
        "image": settings.AI_IMAGE_DAILY_QUOTA,
        "video": settings.AI_VIDEO_DAILY_QUOTA,
        "search": settings.AI_SEARCH_DAILY_QUOTA,
        "extraction": settings.AI_EXTRACTION_DAILY_QUOTA,
        "research": settings.AI_RESEARCH_DAILY_QUOTA,
    }
    for capability, daily_limit in defaults.items():
        result[capability]["daily_limit"] = daily_limit
        result[capability]["daily_window"] = daily_window
    for quota in media:
        result[quota.capability] = {
            "daily_limit": quota.daily_limit,
            "daily_used": quota.daily_used,
            "reserved": quota.reserved,
            "daily_window": quota.daily_window.isoformat() if quota.daily_window else None,
        }
    result["chat"]["daily_window"] = chat.daily_window.isoformat() if chat.daily_window else None
    return {"user_id": user_id, "quotas": result}


@router.put("/users/{user_id}/quotas/{capability}")
def set_capability_quota(
    user_id: int,
    capability: str,
    update: CapabilityQuotaUpdate,
    _: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    if capability != update.capability:
        raise HTTPException(status_code=400, detail="Capability in path and body must match")
    if db.query(User).filter(User.id == user_id).first() is None:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        quota = QuotaService().set_capability_limits(db, user_id=user_id, capability=capability, daily_limit=update.daily_limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"user_id": user_id, "capability": quota.capability, "daily_limit": quota.daily_limit, "daily_used": quota.daily_used, "reserved": quota.reserved, "daily_window": quota.daily_window.isoformat() if quota.daily_window else None}


@router.get("/providers/openrouter/models")
def discover_openrouter_models(_: User = Depends(get_current_admin)):
    try:
        return {"models": discover_free_models()}
    except Exception as exc:  # noqa: BLE001 - redact provider/network details
        raise HTTPException(status_code=502, detail="OpenRouter model discovery failed") from exc


@router.get("/providers/models")
def list_provider_models(_: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    rows = db.query(ProviderModel, Provider).join(Provider, Provider.id == ProviderModel.provider_id).order_by(Provider.name, ProviderModel.model).all()
    return [
        {
            "id": model.id,
            "provider": provider.name,
            "provider_enabled": provider.enabled,
            "model": model.model,
            "enabled": model.enabled,
            "is_free": model.is_free,
            "capabilities": model.capabilities or [],
            "availability": model.availability,
            "verified_at": model.verified_at.isoformat() if model.verified_at else None,
            "max_input_tokens": model.max_input_tokens,
            "max_output_tokens": model.max_output_tokens,
            "restrictions": model.restrictions or [],
        }
        for model, provider in rows
    ]


@router.put("/providers/models")
def upsert_provider_model(
    update: ProviderModelUpdate,
    _: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    if update.enabled and (not update.is_free or update.availability != "available" or update.verified_at is None):
        raise HTTPException(status_code=400, detail="Enabled models must be explicitly verified as available and free")
    provider = db.query(Provider).filter(Provider.name == update.provider).first()
    if provider is None:
        provider = Provider(name=update.provider, enabled=False, free_only=True)
        db.add(provider)
        db.flush()
    model = db.query(ProviderModel).filter(ProviderModel.provider_id == provider.id, ProviderModel.model == update.model).first()
    if model is None:
        model = ProviderModel(provider_id=provider.id, model=update.model)
        db.add(model)
    provider.enabled = update.provider_enabled
    for field in ("enabled", "is_free", "capabilities", "availability", "verified_at", "max_input_tokens", "max_output_tokens", "restrictions"):
        setattr(model, field, getattr(update, field))
    db.commit()
    db.refresh(model)
    return {"id": model.id, "provider": provider.name, "model": model.model, "enabled": model.enabled}


def serialize_admin_room(room: Room, db: Session) -> dict:
    members = db.query(User).filter(User.id.in_(room.members or [])).all() if room.members else []
    last_message = db.query(Message).filter(Message.room_id == room.id).order_by(Message.id.desc()).first()
    return {
        "id": room.id,
        "name": room.name,
        "room_type": room.room_type,
        "members": [{"id": user.id, "name": user.full_name, "email": user.email, "role": user.role} for user in members],
        "last_message_at": last_message.created_at.isoformat() if last_message and last_message.created_at else None,
    }


def record_audit(db: Session, admin: User, action: str, room_id: int | None = None) -> None:
    db.add(AdminAuditLog(admin_id=admin.id, action=action, room_id=room_id))
    db.commit()


@router.get("/chats")
def list_all_chats(
    room_name: str | None = Query(default=None),
    username: str | None = Query(default=None),
    role: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    rooms = db.query(Room).filter(Room.is_active.is_(True)).order_by(Room.id.desc()).all()
    results = []
    for room in rooms:
        members = db.query(User).filter(User.id.in_(room.members or [])).all() if room.members else []
        messages = db.query(Message).filter(Message.room_id == room.id).all()
        pending_invitees = db.query(User).join(RoomInvite, RoomInvite.invited_user_id == User.id).filter(
            RoomInvite.room_id == room.id,
            RoomInvite.status == "pending",
        ).all()
        searchable_users = members + pending_invitees + [db.query(User).filter(User.id == message.sender_id).first() for message in messages]
        searchable_users = [user for user in searchable_users if user is not None]
        if room_name and room_name.lower() not in room.name.lower():
            continue
        # Username and role must match the *same* person: otherwise "doctor" from
        # one member and "sharma" from another would wrongly keep the room.
        if username or role:
            def matches(user: User) -> bool:
                if username and username.lower() not in (user.full_name or "").lower() and username.lower() not in (user.email or "").lower():
                    return False
                if role and (user.role or "").lower() != role.lower():
                    return False
                return True

            if not any(matches(user) for user in searchable_users):
                continue
        results.append(serialize_admin_room(room, db))
    record_audit(db, current_admin, "list_chats")
    return {"items": results[offset: offset + limit], "total": len(results), "limit": limit, "offset": offset}


@router.get("/chats/{room_id}")
def inspect_chat(
    room_id: int,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    room = db.query(Room).filter(Room.id == room_id).first()
    if room is None or not room.is_active:
        # A deleted room leaves the surveillance surface entirely (PRV-05).
        raise HTTPException(status_code=404, detail="Room not found")
    members = db.query(User).filter(User.id.in_(room.members or [])).all() if room.members else []
    messages = db.query(Message).filter(Message.room_id == room.id).order_by(Message.id.asc()).all()
    record_audit(db, current_admin, "view_transcript", room.id)
    return {
        **serialize_admin_room(room, db),
        "messages": [{
            "id": message.id,
            "sender_id": message.sender_id,
            "sender_name": message.sender_name,
            "sender_role": message.sender_role,
            "content": message.content,
            "created_at": message.created_at.isoformat() if message.created_at else None,
        } for message in messages],
        "member_count": len(members),
    }


@router.get("/audit/export")
def export_audit_csv(current_admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    logs = db.query(AdminAuditLog).order_by(AdminAuditLog.created_at.desc()).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "admin_id", "action", "room_id", "created_at"])
    for log in logs:
        writer.writerow([log.id, log.admin_id, log.action, log.room_id or "", log.created_at.isoformat() if log.created_at else ""])
    record_audit(db, current_admin, "export_audit_csv")
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="admin_audit_log.csv"'},
    )


class FeaturePermissionUpdate(BaseModel):
    feature: str
    enabled: bool


@router.get("/video/users")
def list_video_users(_: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    users = db.query(User).order_by(User.created_at.desc()).all()
    result = []
    for user in users:
        permissions = {
            permission.feature: permission.enabled
            for permission in db.query(UserFeaturePermission).filter(UserFeaturePermission.user_id == user.id).all()
        }
        result.append(
            {
                **serialize_user(user),
                "video_generation": permissions.get("video_generation", False),
                "video_editing": permissions.get("video_editing", False),
                "video_understanding": permissions.get("video_understanding", False),
                "image_generation": permissions.get("image_generation", False),
            }
        )
    return result


@router.patch("/video/users/{user_id}/feature")
def toggle_video_feature(
    user_id: int,
    update: FeaturePermissionUpdate,
    _: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    if update.feature not in {"image_generation", "video_generation", "video_editing", "video_understanding"}:
        raise HTTPException(status_code=400, detail="Unsupported media feature")
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    permission = (
        db.query(UserFeaturePermission)
        .filter(UserFeaturePermission.user_id == user_id, UserFeaturePermission.feature == update.feature)
        .first()
    )
    if permission is None:
        permission = UserFeaturePermission(user_id=user_id, feature=update.feature, enabled=update.enabled)
        db.add(permission)
    else:
        permission.enabled = update.enabled
    db.commit()
    db.refresh(permission)
    return {"user_id": user_id, "feature": update.feature, "enabled": permission.enabled}

# ---------------------------------------------------------------------------
# Phase 9 -- provider routing, analytics, logs and feature toggles
# ---------------------------------------------------------------------------

from app.models.gateway import AdminSetting, UsageLog  # noqa: E402
from app.services.ai_gateway import AIGateway  # noqa: E402


class ProviderOrderUpdate(BaseModel):
    order: list[str] = Field(default_factory=list)


class FeatureToggleUpdate(BaseModel):
    key: str = Field(pattern="^(research|images|documents|office|voice|video_generation)$")
    enabled: bool


@router.get("/providers")
def list_providers(_: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    providers = db.query(Provider).order_by(Provider.name).all()
    return [
        {
            "id": provider.id,
            "name": provider.name,
            "enabled": provider.enabled,
            "free_only": provider.free_only,
            "models": db.query(ProviderModel).filter(ProviderModel.provider_id == provider.id).count(),
        }
        for provider in providers
    ]


@router.patch("/providers/{provider_id}")
def toggle_provider(
    provider_id: int,
    enabled: bool = Query(...),
    _: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    provider = db.query(Provider).filter(Provider.id == provider_id).first()
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    provider.enabled = enabled
    db.commit()
    return {"id": provider.id, "name": provider.name, "enabled": provider.enabled}


@router.get("/gateway/order")
def get_gateway_order(_: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    setting = db.query(AdminSetting).filter(AdminSetting.key == "gateway_order").first()
    gateway = AIGateway()
    return {
        "order": (setting.value or {}).get("order") if setting and isinstance(setting.value, dict) else list(gateway.preferences()),
        "allowed": list(gateway.preferences()),
        "mode": gateway.mode,
    }


@router.put("/gateway/order")
def set_gateway_order(update: ProviderOrderUpdate, _: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    gateway = AIGateway()
    allowed = set(gateway.preferences())
    unknown = [name for name in update.order if name.lower() not in allowed]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Provider(s) not in the free allow-list: {', '.join(unknown)}",
        )
    if not update.order:
        raise HTTPException(status_code=400, detail="Provide at least one provider")
    setting = db.query(AdminSetting).filter(AdminSetting.key == "gateway_order").first()
    value = {"order": [name.lower() for name in update.order]}
    if setting is None:
        setting = AdminSetting(key="gateway_order", value=value)
        db.add(setting)
    else:
        setting.value = value
    db.commit()
    return value


@router.get("/analytics/usage")
def usage_analytics(
    days: int = Query(default=7, ge=1, le=90),
    _: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    from datetime import timedelta

    since = datetime.utcnow() - timedelta(days=days)
    rows = db.query(UsageLog).filter(UsageLog.created_at >= since).all()
    by_provider: dict[str, int] = {}
    by_day: dict[str, int] = {}
    for row in rows:
        by_provider[row.provider] = by_provider.get(row.provider, 0) + row.units
        day = row.created_at.date().isoformat() if row.created_at else "unknown"
        by_day[day] = by_day.get(day, 0) + row.units
    return {
        "days": days,
        "total_requests": len(rows),
        "total_units": sum(row.units for row in rows),
        "by_provider": by_provider,
        "by_day": dict(sorted(by_day.items())),
        "unique_users": len({row.user_id for row in rows if row.user_id}),
    }


@router.get("/provider-budgets")
def provider_budgets(_: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """Expose budget state without exposing credentials or provider payloads."""
    rows = db.query(ProviderBudget).order_by(ProviderBudget.provider, ProviderBudget.bucket).all()
    def has_credentials(provider: str) -> bool:
        try:
            return bool(get_provider_credentials(provider))
        except Exception:  # noqa: BLE001 - readiness must never expose provider errors
            return False

    image_cloudflare = bool(
        settings.CLOUDFLARE_ACCOUNT_ID
        and settings.CLOUDFLARE_API_TOKEN
        and settings.CLOUDFLARE_IMAGE_PRIMARY_MODEL
    )
    image_route = bool(
        image_cloudflare
        and settings.CLOUDFLARE_IMAGE_MODEL_VERIFIED
        and settings.CLOUDFLARE_IMAGE_REQUEST_NEURONS > 0
    )
    research_cloudflare = bool(
        settings.CLOUDFLARE_ACCOUNT_ID
        and settings.CLOUDFLARE_API_TOKEN
        and settings.CLOUDFLARE_RESEARCH_MODEL
        and settings.CLOUDFLARE_RESEARCH_MODEL_VERIFIED
        and settings.CLOUDFLARE_RESEARCH_REQUEST_NEURONS > 0
    )
    return {
        "configured_limits": {
            "muse_spark_monthly_usd": settings.MUSE_SPARK_MONTHLY_USD,
            "muse_image_monthly_usd": settings.MUSE_IMAGE_MONTHLY_USD,
            "cloudflare_daily_neurons": settings.CLOUDFLARE_DAILY_NEURON_LIMIT,
            "cloudflare_bucket_share": 0.5,
        },
        "readiness": {
            "chat": {
                "openrouter": has_credentials("OpenRouter"),
                "groq": bool(has_credentials("Groq") and settings.GROQ_MODEL),
                "gemini": has_credentials("Gemini"),
            },
            "research": {
                "search_credentials": bool(settings.TAVILY_API_KEY_1 or settings.TAVILY_API_KEY_2),
                "cloudflare_analysis": research_cloudflare,
                "muse_analysis": bool(settings.MUSE_SPARK_VERIFIED and settings.MUSE_API_URL and settings.MUSE_API_KEY and settings.MUSE_SPARK_MODEL and settings.MUSE_SPARK_REQUEST_COST_USD > 0),
            },
            "image": {
                "cloudflare_generation": image_route,
                "cloudflare_editing": bool(image_route and settings.CLOUDFLARE_IMAGE_EDITING_VERIFIED),
                "metering_units": settings.CLOUDFLARE_IMAGE_REQUEST_NEURONS,
                "orchestration_enabled": settings.IMAGE_ORCHESTRATION_ENABLED,
            },
        },
        "buckets": [
            {
                "provider": row.provider,
                "bucket": row.bucket,
                "period": row.period,
                "window_key": row.window_key,
                "limit_units": row.limit_units,
                "used_units": row.used_units,
                "reserved_units": row.reserved_units,
                "unit_name": row.unit_name,
            }
            for row in rows
        ],
    }


@router.get("/logs")
def list_usage_logs(
    limit: int = Query(default=50, ge=1, le=200),
    provider: str | None = Query(default=None),
    _: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    query = db.query(UsageLog).order_by(UsageLog.id.desc())
    if provider:
        query = query.filter(UsageLog.provider == provider)
    return [
        {
            "id": row.id,
            "user_id": row.user_id,
            "provider": row.provider,
            "model": row.model,
            "status": row.status,
            "units": row.units,
            "request_id": row.request_id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "detail": row.detail,
        }
        for row in query.limit(limit).all()
    ]


@router.get("/settings")
def list_settings(_: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    setting = db.query(AdminSetting).filter(AdminSetting.key == "feature_toggles").first()
    return {"features": (setting.value or {}) if setting and isinstance(setting.value, dict) else {}}


@router.put("/settings/features")
def set_feature_toggle(update: FeatureToggleUpdate, _: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    setting = db.query(AdminSetting).filter(AdminSetting.key == "feature_toggles").first()
    value = dict(setting.value or {}) if setting and isinstance(setting.value, dict) else {}
    value[update.key] = update.enabled
    if setting is None:
        setting = AdminSetting(key="feature_toggles", value=value)
        db.add(setting)
    else:
        setting.value = value
    db.commit()
    return {"features": value}

# ---------------------------------------------------------------------------
# Live provider probe: an admin "Test" runs a real, minimal call so a model is
# only marked verified when the provider actually answered.
# ---------------------------------------------------------------------------

import time as _time  # noqa: E402

from app.services.ai_gateway.gateway import Route  # noqa: E402
from app.services.ai_gateway.providers import GatewayProviderError  # noqa: E402


class VerifyModelRequest(BaseModel):
    provider: str = Field(min_length=2, max_length=80)
    model: str = Field(min_length=2, max_length=160)
    capability: str = Field(default="chat", max_length=40)


@router.post("/providers/verify")
def verify_model(
    payload: VerifyModelRequest,
    _: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Probe a provider/model with one tiny real request.

    On success the model row is marked verified (free + available) with a
    timestamp; on failure the reason is returned and nothing is enabled.
    """
    provider = payload.provider.strip().lower()
    gateway = AIGateway()
    if provider not in gateway.preferences():
        raise HTTPException(
            status_code=400,
            detail=f"{provider} is not in the free allow-list ({', '.join(gateway.preferences())}).",
        )
    route = Route(provider, payload.model.strip())
    started = _time.monotonic()
    try:
        adapter = gateway.provider(route)
        result = adapter.generate("Reply with the single word: OK", system_prompt="Answer briefly.", model=route.model)
        text = getattr(result, "text", result)
        answer = str(text).strip()
        if not answer:
            raise HTTPException(status_code=502, detail="Provider answered with an empty response")
    except GatewayUnavailableError as exc:
        raise HTTPException(status_code=409, detail=f"{provider} is not usable yet: {exc}") from exc
    except GatewayProviderError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Probe failed against {provider}: {exc} (status {exc.status_code or 'n/a'})",
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - surface any adapter error to the admin
        raise HTTPException(status_code=502, detail=f"Probe failed: {type(exc).__name__}: {exc}") from exc

    latency_ms = int((_time.monotonic() - started) * 1000)
    provider_row = db.query(Provider).filter(Provider.name.ilike(provider)).first()
    if provider_row is None:
        provider_row = Provider(name=provider, enabled=False, free_only=True)
        db.add(provider_row)
        db.flush()
    model_row = (
        db.query(ProviderModel)
        .filter(ProviderModel.provider_id == provider_row.id, ProviderModel.model == route.model)
        .first()
    )
    if model_row is None:
        model_row = ProviderModel(provider_id=provider_row.id, model=route.model, capabilities=[payload.capability])
        db.add(model_row)
    model_row.availability = "available"
    model_row.verified_at = datetime.utcnow()
    model_row.is_free = True
    db.commit()
    return {
        "provider": provider,
        "model": route.model,
        "verified": True,
        "latency_ms": latency_ms,
        "sample": answer[:80],
        "message": "Probe succeeded. The model is now verified — enable it to put it in the routing pool.",
    }
