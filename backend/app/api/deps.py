from fastapi import Depends, HTTPException, Path
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core import security
from app.core.config import settings
from app.models.user import User as UserModel
from app.models.room import Room

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

def get_current_user(
    db: Session = Depends(get_db), token: str = Depends(oauth2_scheme)
) -> UserModel:
    credentials_exception = HTTPException(
        status_code=401,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = security.jwt.decode(
            token, security.JWT_SECRET, algorithms=[security.ALGORITHM]
        )
        identifier: str = payload.get("sub")
        if identifier is None:
            raise credentials_exception
    except Exception:
        raise credentials_exception
    
    # The database row is read on every request, so a role change (e.g. a revoked
    # admin) takes effect on the very next call instead of when the token expires.
    user = db.query(UserModel).filter(
        (UserModel.identifier == identifier) | (UserModel.email == identifier)
    ).first()
    if user is None or not user.is_active:
        raise credentials_exception
    return user


def get_current_admin(current_user: UserModel = Depends(get_current_user)) -> UserModel:
    """Allow only active users explicitly marked as administrators.

    The check runs against the user row loaded from the database *in this
    request*, so revoking a user's admin flag takes effect on their very next
    call even with an unexpired bearer token (ADM-CR-13).
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Administrator access required")
    return current_user


def require_legacy_ai_enabled() -> None:
    """Gate the legacy direct-provider endpoints (/ai/*, /chat/multimodal, /chat/stream).

    These bypass the AI gateway (no quota reservation, no free-provider policy), so
    they are disabled by default. Enable with LEGACY_DIRECT_AI_ENABLED=true only
    after an explicit decision to allow direct provider calls.
    """
    if not getattr(settings, "LEGACY_DIRECT_AI_ENABLED", False):
        raise HTTPException(
            status_code=410,
            detail=(
                "This legacy endpoint is disabled. Use /api/v1/chat/v2 (quota-checked, "
                "free-provider gateway) instead."
            ),
        )


def require_room_member(
    room_id: int = Path(...),
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Room:
    """Load a room and enforce membership for regular room operations."""
    room = db.query(Room).filter(Room.id == room_id, Room.is_active.is_(True)).first()
    if room is None:
        raise HTTPException(status_code=404, detail="Room not found")
    if current_user.id not in (room.members or []):
        raise HTTPException(status_code=403, detail="You are not a member of this room")
    return room