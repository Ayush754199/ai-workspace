from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from fastapi.security import OAuth2PasswordBearer
from pymongo.collation import Collation
from app.core.database import get_db, Base, engine
from app.core.config import settings
from app.models.user import User as UserModel
from app.core import security
from app.schemas.user import UserCreate, UserResponse, Token, LoginRequest, SignupRequest, normalize_mobile_number
from app.db.session import mongo

try:
    from backend.logger import log_connection_failure
except ModuleNotFoundError:
    from logger import log_connection_failure

router = APIRouter(prefix="/auth", tags=["auth"])

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_user_by_email(db: Session, email: str):
    return db.query(UserModel).filter(UserModel.email == email).first()


def get_user_by_mobile(db: Session, mobile_number: str):
    return db.query(UserModel).filter(UserModel.mobile_number == mobile_number).first()


def get_user_by_identifier(db: Session, identifier: str):
    return db.query(UserModel).filter(
        (UserModel.identifier == identifier)
        | (UserModel.email == identifier)
        | (UserModel.mobile_number == identifier)
    ).first()


def user_response(user: UserModel) -> dict:
    return {**user.__dict__, "created_at": user.created_at.isoformat() if user.created_at else None}


def mongo_response(document: dict) -> dict:
    created_at = document.get("created_at")
    return {
        "id": str(document.get("id") or document.get("_id") or ""),
        "email": document.get("email"),
        "mobile_number": document.get("mobile_number"),
        "identifier": document.get("identifier"),
        "full_name": document.get("full_name"),
        "dob": document.get("dob"),
        "role": document.get("role", "user"),
        "is_active": document.get("is_active", True),
        "is_admin": document.get("is_admin", False),
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at or ""),
    }


async def mongo_user(identifier: str):
    if not mongo.enabled:
        return None
    await mongo.verify_ready()
    try:
        return await mongo.users.find_one({"$or": [{"identifier": identifier}, {"email": identifier}, {"mobile_number": identifier}]})
    except Exception as exc:
        log_connection_failure(
            exc,
            endpoint="mongodb://Atlas",
            payload={"database": "users", "operation": "find_one", "identifier": identifier},
        )
        raise


async def ensure_mongo_indexes() -> None:
    if mongo.enabled:
        await mongo.verify_ready()
        try:
            await mongo.users.create_index(
                [("username", 1)],
                name="username_case_insensitive_unique",
                unique=True,
                collation=Collation(locale="en", strength=2),
                partialFilterExpression={"username": {"$type": "string"}},
            )
            await mongo.users.create_index("identifier", unique=True, sparse=True)
            await mongo.users.create_index("email", unique=True, sparse=True)
            await mongo.users.create_index("mobile_number", unique=True, sparse=True)
        except Exception as exc:
            log_connection_failure(
                exc,
                endpoint="mongodb://Atlas",
                payload={
                    "collection": "users",
                    "operation": "create_index",
                    "fields": ["username", "identifier", "email"],
                },
            )
            raise


def mirror_to_sql(
    db: Session,
    identifier: str,
    password_hash: str,
    full_name: str,
    dob: str | None,
    *,
    mongo_id: str | None = None,
    email: str | None = None,
    mobile_number: str | None = None,
    role: str = "user",
    is_active: bool = True,
    is_admin: bool = False,
) -> UserModel:
    """Idempotently project one Mongo identity into SQL authorization tables.

    MongoDB remains the source of truth for legacy profile/session data. SQL stores
    the stable mapping and authorization projection used by FastAPI dependencies.
    """
    user = get_user_by_identifier(db, identifier)
    if user is None and mongo_id:
        user = db.query(UserModel).filter(UserModel.mongo_id == mongo_id).first()
    if user is None:
        user = UserModel(identifier=identifier, email=email or (identifier if "@" in identifier else None), hashed_password=password_hash)
        db.add(user)
    user.identifier = identifier
    user.email = email or user.email or (identifier if "@" in identifier else None)
    user.mobile_number = mobile_number or user.mobile_number or (identifier if identifier.isdigit() else None)
    user.hashed_password = password_hash or user.hashed_password
    user.full_name = full_name
    user.dob = dob
    user.mongo_id = mongo_id or user.mongo_id
    user.role = role or "user"
    user.is_active = is_active
    user.is_admin = is_admin
    db.commit()
    db.refresh(user)
    return user


@router.post("/signup", response_model=Token)
async def signup(request: SignupRequest, db: Session = Depends(get_db)):
    identifier = request.identifier.strip()
    # SQL is authoritative. Authenticate/check the local identity row first so an
    # optional Mongo mirror outage cannot block registration (and do not pay a
    # Mongo ping on the common duplicate-identifier path).
    if get_user_by_identifier(db, identifier):
        raise HTTPException(status_code=400, detail="Identifier already registered")
    mongo_record = None
    try:
        mongo_record = await mongo_user(identifier)
    except Exception as exc:  # noqa: BLE001 - SQL registration continues
        log_connection_failure(
            exc, endpoint="mongodb://Atlas",
            payload={"collection": "users", "operation": "signup_lookup"},
        )
    if mongo_record:
        raise HTTPException(status_code=400, detail="Identifier already registered")
    password_hash = security.hash_password(request.password)
    created_at = datetime.now(timezone.utc)
    if mongo.enabled:
        try:
            await ensure_mongo_indexes()
            # Check for a Mongo-only legacy account only when the mirror is healthy.
            # Failure is logged, then registration continues to SQL.
            if await mongo.users.find_one({"$or": [{"identifier": identifier}, {"email": identifier}, {"mobile_number": identifier}]}):
                raise HTTPException(status_code=400, detail="Identifier already registered")
            await mongo.users.insert_one({
                "identifier": identifier,
                "email": identifier if "@" in identifier else None,
                "hashed_password": password_hash,
                "full_name": request.full_name.strip(),
                "dob": request.dob,
                "role": "user",
                "is_active": True,
                "is_admin": False,
                "created_at": created_at,
            })
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 - SQL registration is authoritative
            log_connection_failure(
                exc,
                endpoint="mongodb://Atlas",
                payload={"collection": "users", "operation": "insert_one"},
            )
            # Continue with the authoritative SQL row. Mongo is an optional legacy
            # profile mirror and may be unavailable during registration.
    mirror_to_sql(db, identifier, password_hash, request.full_name.strip(), request.dob, email=identifier if "@" in identifier else None)
    return {"access_token": security.create_access_token(data={"sub": identifier}), "token_type": "bearer"}


@router.post("/register", response_model=UserResponse)
async def register(user_in: UserCreate, db: Session = Depends(get_db)):
    # SQL is authoritative for registration as well as login. Mongo is a best-effort
    # profile mirror: an Atlas outage must not prevent a fresh SQL identity.
    existing = get_user_by_email(db, user_in.email) or get_user_by_mobile(db, user_in.mobile_number)
    if existing:
        raise HTTPException(status_code=400, detail="Email or mobile number already registered")
    hashed_password = security.hash_password(user_in.password)
    if mongo.enabled:
        mirror_ready = False
        try:
            await mongo.verify_ready()
            mirror_ready = True
            await ensure_mongo_indexes()
            if await mongo.users.find_one({"$or": [{"email": user_in.email}, {"mobile_number": user_in.mobile_number}]}):
                raise HTTPException(status_code=400, detail="Email or mobile number already registered")
        except HTTPException as exc:
            if exc.status_code == 400:
                raise
            log_connection_failure(
                exc, endpoint="mongodb://Atlas",
                payload={"collection": "users", "operation": "register_lookup"},
            )
            mirror_ready = False
        except Exception as exc:  # noqa: BLE001 - optional mirror only
            log_connection_failure(
                exc, endpoint="mongodb://Atlas",
                payload={"collection": "users", "operation": "register_lookup"},
            )
            mirror_ready = False
        if mirror_ready:
            try:
                await mongo.users.insert_one({"identifier": user_in.email, "email": user_in.email, "mobile_number": user_in.mobile_number, "hashed_password": hashed_password, "full_name": user_in.full_name, "dob": user_in.dob, "role": "user", "is_active": True, "is_admin": False, "created_at": datetime.now(timezone.utc)})
            except Exception as exc:  # noqa: BLE001 - SQL remains authoritative
                log_connection_failure(
                    exc, endpoint="mongodb://Atlas",
                    payload={"collection": "users", "operation": "register_insert"},
                )
    user = UserModel(identifier=user_in.email, email=user_in.email, mobile_number=user_in.mobile_number, hashed_password=hashed_password, full_name=user_in.full_name, dob=user_in.dob, is_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserResponse.model_validate({
        **user_response(user)
    })


@router.post("/login", response_model=Token)
async def login(form_data: LoginRequest, db: Session = Depends(get_db)):
    identifier = form_data.identifier or form_data.email or form_data.mobile_number
    if identifier and "@" not in identifier:
        try:
            identifier = normalize_mobile_number(identifier)
        except ValueError:
            pass
    # SQL holds the password hash and is authoritative. Avoid a Mongo network round
    # trip before verifying credentials; the optional mirror is only consulted when
    # no SQL row exists (legacy users created through the old Mongo-only UI).
    user = get_user_by_identifier(db, identifier) if identifier else None
    mongo_record = None
    if user is None and identifier and mongo.enabled:
        try:
            mongo_record = await mongo_user(identifier)
        except Exception as exc:  # noqa: BLE001 - Mongo is an optional mirror
            log_connection_failure(
                exc,
                endpoint="mongodb://Atlas",
                payload={"collection": "users", "operation": "login_fallback"},
            )
    stored_hash = mongo_record.get("hashed_password") if mongo_record else (user.hashed_password if user else None)

    def _authorization_row(current):
        """SQL is the authorization source of truth.

        With Mongo enabled, login used to copy ``role``/``is_admin``/``is_active``
        back from the Mongo profile on every sign-in, which silently undid an
        admin promotion or revocation made in SQL. Only create a missing row; keep
        the existing authorization columns untouched.

        Administrator rights are NEVER granted by this function. There used to be a
        hard-coded identifier here that promoted (and on first login even created)
        an administrator with no environment configuration, so anybody who knew the
        number could take over a deployment. Promotion is now an explicit,
        environment-controlled operation performed once at startup by
        ``app.lifespan.provision_configured_admin`` (ADMIN_BOOTSTRAP_IDENTIFIER).
        Existing admins keep their flag; new accounts are always plain users.
        """
        if current is not None:
            return current
        is_mongo_account = mongo_record is not None
        row = UserModel(
            identifier=(str(mongo_record.get("identifier") or identifier) if is_mongo_account else identifier),
            email=(mongo_record.get("email") if is_mongo_account else None) or (identifier if "@" in identifier else None),
            mobile_number=(mongo_record.get("mobile_number") if is_mongo_account else None),
            hashed_password=stored_hash or "",
            full_name=(mongo_record.get("full_name") if is_mongo_account else None),
            dob=(mongo_record.get("dob") if is_mongo_account else None),
            mongo_id=(str(mongo_record.get("_id") or mongo_record.get("id") or "") or None) if is_mongo_account else None,
            is_admin=False,
            role="user",
            is_active=True,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    if not stored_hash or not security.verify_password(form_data.password, stored_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = _authorization_row(user)
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is disabled",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = security.create_access_token(data={"sub": user.identifier if user else identifier})
    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/me", response_model=UserResponse)
async def me(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = security.decode_access_token(token)
        identifier: str = payload.get("sub")
        if identifier is None:
            raise credentials_exception
    except Exception:
        raise credentials_exception
    # SQL is the identity/authorization source of truth here. MongoDB only mirrors
    # legacy profile data, so an outage must not turn a valid session into a 401 or
    # a 500. A row absent from SQL means the account was never provisioned.
    user = get_user_by_identifier(db, identifier)
    if user is None or not user.is_active:
        raise credentials_exception
    # Authorization is read from SQL in the same request: a revoked admin loses
    # access immediately even though their bearer token is still valid.
    return UserResponse.model_validate({
        **user_response(user)
    })
