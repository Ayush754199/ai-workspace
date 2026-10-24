from pydantic import BaseModel, EmailStr
from typing import Optional


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: Optional[str] = None
    dob: Optional[str] = None


class UserResponse(BaseModel):
    id: int
    email: Optional[str] = None
    identifier: Optional[str] = None
    full_name: Optional[str] = None
    dob: Optional[str] = None
    role: str
    is_active: bool
    is_admin: bool
    created_at: str

    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    email: Optional[EmailStr] = None
    identifier: Optional[str] = None
    password: str


class SignupRequest(BaseModel):
    identifier: str
    password: str
    full_name: str
    dob: str