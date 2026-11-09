from passlib.context import CryptContext
from passlib.exc import UnknownHashError
from jose import JWTError, jwt
from datetime import datetime, timedelta, timezone
from app.core.config import settings

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

# Reuse the already-validated application settings instead of building a second
# ``Settings()``. A separate instance validated the environment again and crashed
# on a short JWT_SECRET with pydantic's raw "string_too_short" error -- even when
# config.settings had already repaired the value -- so the deploy never started.
JWT_SECRET = settings.JWT_SECRET
ALGORITHM = settings.ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES = settings.ACCESS_TOKEN_EXPIRE_MINUTES


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    # Legacy rows can hold hashes from another scheme; an unreadable hash must
    # behave like a wrong password (401), not crash the login endpoint (500).
    try:
        return pwd_context.verify(password, hashed)
    except (ValueError, TypeError, UnknownHashError):
        return False


def create_access_token(data: dict, expires_delta: timedelta = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta if expires_delta else timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET, algorithm=ALGORITHM)
    return encoded_jwt