from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class DependentCreate(BaseModel):
    first_name: str
    last_name: str
    date_of_birth: date
    relationship: str
    months_lived_with: int
    student_status: bool = False
    disability_status: bool = False


class TaxFilingCreate(BaseModel):
    tax_year: int
    filing_type: str
    first_name: str
    last_name: str
    email: EmailStr
    phone: str | None = None

    date_of_birth: date | None = None
    country_of_citizenship: str | None = None
    current_country_of_residence: str | None = None
    us_tax_residency_status: str | None = None
    filing_status: str | None = None
    filed_us_taxes_before: str | None = None
    previous_tax_year_filed: int | None = None

    address_line_1: str | None = None
    address_line_2: str | None = None
    city: str | None = None
    state_province: str | None = None
    postal_code: str | None = None
    country: str | None = None

    spouse_first_name: str | None = None
    spouse_last_name: str | None = None
    spouse_date_of_birth: date | None = None
    spouse_email: EmailStr | None = None
    spouse_phone: str | None = None
    spouse_country_of_citizenship: str | None = None
    spouse_us_tax_residency_status: str | None = None

    dependents: list[dict[str, Any]] = Field(default_factory=list)
    income_categories: list[str] = Field(default_factory=list)
    deductions_credits: list[str] = Field(default_factory=list)
    special_situations: list[str] = Field(default_factory=list)

    has_previous_return_copy: str | None = None
    received_irs_notice: str | None = None
    unresolved_tax_issues: str | None = None
    is_amended_return: str | None = None


class TaxFilingResponse(TaxFilingCreate):
    model_config = ConfigDict(from_attributes=True)
    id: str
    client_id: str
    status: str
