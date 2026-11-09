from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional


class UserCreate(BaseModel):
    email: EmailStr
    mobile_number: str
    password: str
    full_name: Optional[str] = None
    dob: Optional[str] = None

    @field_validator("mobile_number")
    @classmethod
    def validate_mobile_number(cls, value: str) -> str:
        return normalize_mobile_number(value)


class UserResponse(BaseModel):
    id: int | str
    email: Optional[str] = None
    mobile_number: Optional[str] = None
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
    mobile_number: Optional[str] = None
    password: str

    @field_validator("mobile_number")
    @classmethod
    def normalize_mobile(cls, value: Optional[str]) -> Optional[str]:
        return normalize_mobile_number(value) if value else value


class SignupRequest(BaseModel):
    identifier: str
    password: str
    full_name: str
    dob: str


def normalize_mobile_number(value: str) -> str:
    """Keep mobile identity comparisons stable without storing formatting noise."""
    compact = "".join(character for character in value.strip() if character.isdigit() or character == "+")
    if compact.startswith("+"):
        digits = compact[1:]
    else:
        digits = compact
    if not digits.isdigit() or not 7 <= len(digits) <= 15:
        raise ValueError("Enter a valid mobile number with 7 to 15 digits")
    return f"+{digits}" if compact.startswith("+") else digits
