from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


_PHONE_RE = re.compile(r"^[+0-9().\-\s]{7,25}$")
_POSTAL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\s\-]{2,18}$")


def _clean_text(value: str | None, field_name: str, max_length: int) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.split())
    if not cleaned:
        return None
    if len(cleaned) > max_length:
        raise ValueError(f"{field_name} must be {max_length} characters or fewer.")
    return cleaned


class PersonalInformationUpdate(BaseModel):
    first_name: str = Field(min_length=1, max_length=50)
    last_name: str | None = Field(default=None, max_length=50)
    date_of_birth: date | None = None

    @field_validator("first_name")
    @classmethod
    def validate_first_name(cls, value: str) -> str:
        cleaned = _clean_text(value, "First name", 50)
        if not cleaned:
            raise ValueError("Please enter your first name.")
        return cleaned

    @field_validator("last_name")
    @classmethod
    def validate_last_name(cls, value: str | None) -> str | None:
        return _clean_text(value, "Last name", 50)

    @field_validator("date_of_birth")
    @classmethod
    def validate_date_of_birth(cls, value: date | None) -> date | None:
        if value is None:
            return value
        today = date.today()
        age = today.year - value.year - ((today.month, today.day) < (value.month, value.day))
        if value > today or age < 18:
            raise ValueError("You must be at least 18 years old.")
        return value


class AddressInformationUpdate(BaseModel):
    address_line_1: str = Field(min_length=1, max_length=120)
    address_line_2: str | None = Field(default=None, max_length=120)
    city: str = Field(min_length=1, max_length=80)
    state_province: str | None = Field(default=None, max_length=80)
    postal_code: str = Field(min_length=3, max_length=20)
    country: str = Field(min_length=2, max_length=80)

    @field_validator("address_line_1", "address_line_2", "city", "state_province", "country")
    @classmethod
    def validate_text_fields(cls, value: str | None, info) -> str | None:
        cleaned = _clean_text(value, info.field_name.replace("_", " ").title(), 120)
        if info.field_name != "address_line_2" and not cleaned:
            raise ValueError(f"Please enter your {info.field_name.replace('_', ' ')}.")
        return cleaned

    @field_validator("postal_code")
    @classmethod
    def validate_postal_code(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not _POSTAL_RE.fullmatch(cleaned):
            raise ValueError("Please enter a valid postal code.")
        return cleaned


class ContactInformationUpdate(BaseModel):
    phone: str = Field(min_length=7, max_length=25)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not _PHONE_RE.fullmatch(cleaned):
            raise ValueError("Please enter a valid phone number.")
        return cleaned


class EmailChangeRequest(BaseModel):
    email: EmailStr


class ProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    first_name: str
    last_name: str | None
    date_of_birth: date | None
    email: EmailStr
    phone: str
    address_line_1: str | None
    address_line_2: str | None
    city: str | None
    state_province: str | None
    postal_code: str | None
    country: str | None
    completion_percentage: int
    missing_fields: list[str]