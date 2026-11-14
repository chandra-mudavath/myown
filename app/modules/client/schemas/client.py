from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


_PHONE_RE = re.compile(r"^[+0-9().\-\s]{7,25}$")
_POSTAL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\s\-]{2,18}$")


def _clean_name(value: str | None, field_name: str, max_length: int) -> str | None:
    """Taxpayer names: letters, numbers and spaces, at least one letter, like sign-up (client feedback, Nov 2026)."""
    from app.platform.schemas.auth import clean_name
    cleaned = _clean_text(value, field_name, max_length)
    return clean_name(cleaned, field_name, required=False) if cleaned else None


def _clean_text(value: str | None, field_name: str, max_length: int) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.split())
    if not cleaned:
        return None
    if len(cleaned) > max_length:
        raise ValueError(f"{field_name} must be {max_length} characters or fewer.")
    return cleaned


GENDERS = ("male", "female", "other")


class PersonalInformationUpdate(BaseModel):
    """Taxpayer details. All required (client feedback, Nov 2026): SSN, first / middle / last name, gender,
    date of birth and occupation. Middle name may be replaced by ticking "No middle name". A blank SSN keeps
    the one saved before, so the full number never has to be sent back to the browser."""
    model_config = ConfigDict(validate_default=True)  # a missing field is checked too, not skipped

    ssn: str | None = None
    first_name: str = Field(min_length=1, max_length=50)
    middle_name: str | None = Field(default=None, max_length=50)
    no_middle_name: bool = False
    last_name: str | None = Field(default=None, max_length=50)
    gender: str | None = None
    date_of_birth: date | None = None
    occupation: str | None = Field(default=None, max_length=80)

    @field_validator("ssn")
    @classmethod
    def validate_ssn(cls, value: str | None) -> str | None:
        digits = re.sub(r"\D", "", value or "")
        if not digits:
            return None
        if len(digits) != 9 or digits.startswith(("000", "666", "9")) or digits[3:5] == "00" or digits[5:] == "0000":
            raise ValueError("Enter a valid 9-digit SSN, e.g. 123-45-6789.")
        return digits

    @field_validator("first_name")
    @classmethod
    def validate_first_name(cls, value: str) -> str:
        cleaned = _clean_name(value, "First name", 50)
        if not cleaned:
            raise ValueError("Please enter your first name.")
        return cleaned

    @field_validator("middle_name")
    @classmethod
    def validate_middle_name(cls, value: str | None) -> str | None:
        return _clean_name(value, "Middle name", 50)

    @field_validator("last_name", mode="before")
    @classmethod
    def validate_last_name(cls, value: str | None) -> str:
        cleaned = _clean_name(value, "Last name", 50)
        if not cleaned:
            raise ValueError("Please enter your last name.")
        return cleaned

    @field_validator("gender", mode="before")
    @classmethod
    def validate_gender(cls, value: str | None) -> str:
        if value not in GENDERS:
            raise ValueError("Please choose your gender.")
        return value

    @field_validator("date_of_birth", mode="after")
    @classmethod
    def validate_date_of_birth(cls, value: date | None) -> date:
        if value is None:
            raise ValueError("Please enter your date of birth.")
        today = date.today()
        age = today.year - value.year - ((today.month, today.day) < (value.month, value.day))
        if value > today or age < 18:
            raise ValueError("You must be at least 18 years old.")
        return value

    @field_validator("occupation", mode="before")
    @classmethod
    def validate_occupation(cls, value: str | None) -> str:
        cleaned = _clean_text(value, "Occupation", 80)
        if not cleaned:
            raise ValueError("Please enter your occupation.")
        return cleaned

    @model_validator(mode="after")
    def middle_name_or_none(self):
        if self.no_middle_name:
            self.middle_name = None
        elif not self.middle_name:
            raise ValueError('Please enter your middle name, or tick "No middle name".')
        return self


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