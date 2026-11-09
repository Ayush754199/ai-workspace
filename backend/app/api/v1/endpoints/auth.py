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
    await ensure_mongo_indexes()
    if get_user_by_identifier(db, identifier) or await mongo_user(identifier):
        raise HTTPException(status_code=400, detail="Identifier already registered")
    password_hash = security.hash_password(request.password)
    created_at = datetime.now(timezone.utc)
    if mongo.enabled:
        try:
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
        except Exception as exc:
            log_connection_failure(
                exc,
                endpoint="mongodb://Atlas",
                payload={"collection": "users", "operation": "insert_one", "identifier": identifier},
            )
            raise
    mirror_to_sql(db, identifier, password_hash, request.full_name.strip(), request.dob, email=identifier if "@" in identifier else None)
    return {"access_token": security.create_access_token(data={"sub": identifier}), "token_type": "bearer"}


@router.post("/register", response_model=UserResponse)
async def register(user_in: UserCreate, db: Session = Depends(get_db)):
    await mongo.verify_ready()
    existing = get_user_by_email(db, user_in.email) or get_user_by_mobile(db, user_in.mobile_number)
    if existing:
        raise HTTPException(status_code=400, detail="Email or mobile number already registered")
    hashed_password = security.hash_password(user_in.password)
    await ensure_mongo_indexes()
    if mongo.enabled:
        try:
            if await mongo.users.find_one({"$or": [{"email": user_in.email}, {"mobile_number": user_in.mobile_number}]}):
                raise HTTPException(status_code=400, detail="Email or mobile number already registered")
            await mongo.users.insert_one({"identifier": user_in.email, "email": user_in.email, "mobile_number": user_in.mobile_number, "hashed_password": hashed_password, "full_name": user_in.full_name, "dob": user_in.dob, "role": "user", "is_active": True, "is_admin": False, "created_at": datetime.now(timezone.utc)})
        except Exception as exc:
            log_connection_failure(
                exc,
                endpoint="mongodb://Atlas",
                payload={"collection": "users", "operation": "insert_one", "identifier": user_in.email},
            )
            raise
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
    try:
        mongo_record = await mongo_user(identifier) if identifier else None
    except HTTPException:
        # The database mirror is unreachable: answer 503 instead of pretending the
        # password was wrong or hanging until the client times out.
        raise
    except Exception as exc:
        # Mongo is only a mirror of profile data: if it is unreachable (timeouts,
        # outages) SQL still holds a verifiable login record.
        log_connection_failure(
            exc,
            endpoint="mongodb://Atlas",
            payload={"collection": "users", "operation": "login_fallback", "identifier": identifier},
        )
        mongo_record = None
    user = get_user_by_identifier(db, identifier) if identifier else None
    stored_hash = mongo_record.get("hashed_password") if mongo_record else (user.hashed_password if user else None)

    def _authorization_row(current):
        """SQL is the authorization source of truth.

        With Mongo enabled, login used to copy ``role``/``is_admin``/``is_active``
        back from the Mongo profile on every sign-in, which silently undid an
        admin promotion or revocation made in SQL. Only create a missing row; keep
        the existing authorization columns untouched.
        """
        bootstrap_identifier = settings.ADMIN_BOOTSTRAP_IDENTIFIER.strip()
        if current is not None:
            if bootstrap_identifier and identifier == bootstrap_identifier and not current.is_admin:
                current.is_admin = True
                current.role = "admin"
                db.commit()
                db.refresh(current)
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
        if bootstrap_identifier and identifier == bootstrap_identifier:
            row.is_admin = True
            row.role = "admin"
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
        payload = security.jwt.decode(token, security.JWT_SECRET, algorithms=[security.ALGORITHM])
        identifier: str = payload.get("sub")
        if identifier is None:
            raise credentials_exception
    except Exception:
        raise credentials_exception
    user = get_user_by_identifier(db, identifier)
    if user is None or not user.is_active:
        raise credentials_exception
    # Authorization is read from SQL in the same request: a revoked admin loses
    # access immediately even though their bearer token is still valid.
    return UserResponse.model_validate({
        **user_response(user)
    })
