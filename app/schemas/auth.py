"""
Pydantic v2 schemas for authentication.
"""

from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


# ── Validators ────────────────────────────────────────────────────────────────

_NAME_RE = re.compile(r"^[A-Za-z0-9]+$")
_NAME_STRICT_RE = re.compile(r"^[A-Za-z]+$")


def _validate_password_strength(v: str) -> str:
    errors = []
    if len(v) < 8:
        errors.append("at least 8 characters")
    if not re.search(r"[A-Z]", v):
        errors.append("one uppercase letter")
    if not re.search(r"[a-z]", v):
        errors.append("one lowercase letter")
    if not re.search(r"\d", v):
        errors.append("one number")
    if not re.search(r"[\W_]", v):
        errors.append("one special character")
    if errors:
        raise ValueError(f"Password must contain: {', '.join(errors)}")
    return v


# ── Register ──────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    first_name: str = Field(min_length=1, max_length=15)
    last_name: Optional[str] = Field(default=None, max_length=15)
    email: EmailStr
    phone: str = Field(min_length=7, max_length=20)
    password: str = Field(min_length=8)
    password_confirmation: str

    @field_validator("first_name")
    @classmethod
    def validate_first_name(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError("First name must contain only letters and numbers")
        return v.strip()

    @field_validator("last_name")
    @classmethod
    def validate_last_name(cls, v: Optional[str]) -> Optional[str]:
        if v and not _NAME_STRICT_RE.match(v):
            raise ValueError("Last name must contain only letters")
        return v.strip() if v else None

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        return _validate_password_strength(v)

    @model_validator(mode="after")
    def passwords_match(self) -> "RegisterRequest":
        if self.password != self.password_confirmation:
            raise ValueError("Passwords do not match")
        return self


# ── Login ─────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: EmailStr
    password: str


# ── Forgot / Reset password ───────────────────────────────────────────────────

class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8)
    confirm_password: str

    @field_validator("new_password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        return _validate_password_strength(v)

    @model_validator(mode="after")
    def passwords_match(self) -> "ResetPasswordRequest":
        if self.new_password != self.confirm_password:
            raise ValueError("Passwords do not match")
        return self


# ── Email verification ────────────────────────────────────────────────────────

class VerifyEmailRequest(BaseModel):
    token: str


class ResendVerificationRequest(BaseModel):
    email: EmailStr


# ── Responses ─────────────────────────────────────────────────────────────────

class AuthTokenResponse(BaseModel):
    account_type: str
    access_token: str
    token_type: str = "bearer"


class MeResponse(BaseModel):
    account_id: str
    account_number: str
    email: str
    account_type: str
    is_verified: bool

    model_config = {"from_attributes": True}


class GenericResponse(BaseModel):
    message: str


class UserOut(BaseModel):
    id: str
    email: str
    first_name: str
    last_name: Optional[str]
    phone: str
    role: str
    is_active: bool
    model_config = {"from_attributes": True}
