from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from fastapi.security import OAuth2PasswordBearer
from app.core.database import get_db, Base, engine
from app.models.user import User as UserModel
from app.core import security
from app.schemas.user import UserCreate, UserResponse, Token, LoginRequest, SignupRequest
from app.db.session import mongo

router = APIRouter(prefix="/auth", tags=["auth"])

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_user_by_email(db: Session, email: str):
    return db.query(UserModel).filter(UserModel.email == email).first()


def get_user_by_identifier(db: Session, identifier: str):
    return db.query(UserModel).filter(
        (UserModel.identifier == identifier) | (UserModel.email == identifier)
    ).first()


def user_response(user: UserModel) -> dict:
    return {**user.__dict__, "created_at": user.created_at.isoformat() if user.created_at else None}


def mongo_response(document: dict) -> dict:
    created_at = document.get("created_at")
    return {
        "id": document.get("id"),
        "email": document.get("email"),
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
    return await mongo.users.find_one({"$or": [{"identifier": identifier}, {"email": identifier}]})


async def ensure_mongo_indexes() -> None:
    if mongo.enabled:
        await mongo.users.create_index("identifier", unique=True, sparse=True)
        await mongo.users.create_index("email", unique=True, sparse=True)


def mirror_to_sql(db: Session, identifier: str, password_hash: str, full_name: str, dob: str | None) -> None:
    if get_user_by_identifier(db, identifier):
        return
    db.add(UserModel(identifier=identifier, email=identifier, hashed_password=password_hash, full_name=full_name, dob=dob, is_admin=False))
    db.commit()


@router.post("/signup", response_model=Token)
async def signup(request: SignupRequest, db: Session = Depends(get_db)):
    identifier = request.identifier.strip()
    await ensure_mongo_indexes()
    if get_user_by_identifier(db, identifier) or await mongo_user(identifier):
        raise HTTPException(status_code=400, detail="Identifier already registered")
    password_hash = security.hash_password(request.password)
    created_at = datetime.now(timezone.utc)
    if mongo.enabled:
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
    mirror_to_sql(db, identifier, password_hash, request.full_name.strip(), request.dob)
    return {"access_token": security.create_access_token(data={"sub": identifier}), "token_type": "bearer"}


@router.post("/register", response_model=UserResponse)
async def register(user_in: UserCreate, db: Session = Depends(get_db)):
    existing = get_user_by_email(db, user_in.email)
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    hashed_password = security.hash_password(user_in.password)
    await ensure_mongo_indexes()
    if mongo.enabled:
        await mongo.users.insert_one({"identifier": user_in.email, "email": user_in.email, "hashed_password": hashed_password, "full_name": user_in.full_name, "dob": user_in.dob, "role": "user", "is_active": True, "is_admin": False, "created_at": datetime.now(timezone.utc)})
    user = UserModel(identifier=user_in.email, email=user_in.email, hashed_password=hashed_password, full_name=user_in.full_name, dob=user_in.dob, is_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserResponse.model_validate({
        **user_response(user)
    })


@router.post("/login", response_model=Token)
async def login(form_data: LoginRequest, db: Session = Depends(get_db)):
    identifier = form_data.identifier or form_data.email
    mongo_record = await mongo_user(identifier) if identifier else None
    user = get_user_by_identifier(db, identifier) if identifier and not mongo_record else None
    stored_hash = mongo_record.get("hashed_password") if mongo_record else (user.hashed_password if user else None)
    if not stored_hash or not security.verify_password(form_data.password, stored_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = security.create_access_token(data={"sub": (mongo_record or {}).get("identifier") or user.identifier or user.email})
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
    record = await mongo_user(identifier)
    if record:
        return mongo_response(record)
    user = get_user_by_identifier(db, identifier)
    if user is None:
        raise credentials_exception
    return UserResponse.model_validate({
        **user_response(user)
    })