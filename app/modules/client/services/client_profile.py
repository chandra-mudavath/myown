from __future__ import annotations

from sqlalchemy.orm import Session

from app.platform.models.auth import AuthAccount
from app.modules.client.models.client import Client
from app.modules.client.schemas.client import AddressInformationUpdate, ContactInformationUpdate, PersonalInformationUpdate


COMPLETION_FIELDS = (
    ("First name", "first_name"),
    ("Last name", "last_name"),
    ("Date of birth", "date_of_birth"),
    ("Email address", "email"),
    ("Phone number", "phone"),
    ("Address line 1", "address_line_1"),
    ("City", "city"),
    ("State / Province", "state_province"),
    ("ZIP / Postal code", "postal_code"),
    ("Country", "country"),
)


def get_client_for_account(db: Session, account_id: str) -> Client | None:
    return db.query(Client).filter(Client.account_id == account_id).first()


def _profile_values(client: Client, account: AuthAccount) -> dict[str, object]:
    values = {field: getattr(client, field, None) for _, field in COMPLETION_FIELDS if field != "email"}
    values["email"] = account.email
    return values


def profile_completion(client: Client, account: AuthAccount) -> tuple[int, list[str]]:
    values = _profile_values(client, account)
    missing = [label for label, field in COMPLETION_FIELDS if not values.get(field)]
    completed = len(COMPLETION_FIELDS) - len(missing)
    return round(completed / len(COMPLETION_FIELDS) * 100), missing


def serialize_profile(client: Client, account: AuthAccount) -> dict[str, object]:
    percentage, missing = profile_completion(client, account)
    return {
        "first_name": client.first_name,
        "last_name": client.last_name,
        "date_of_birth": client.date_of_birth.isoformat() if client.date_of_birth else None,
        "email": account.email,
        "phone": client.phone,
        "address_line_1": client.address_line_1,
        "address_line_2": client.address_line_2,
        "city": client.city,
        "state_province": client.state_province,
        "postal_code": client.postal_code,
        "country": client.country,
        "completion_percentage": percentage,
        "missing_fields": missing,
    }


def update_personal(client: Client, data: PersonalInformationUpdate) -> None:
    client.first_name = data.first_name
    client.last_name = data.last_name
    client.date_of_birth = data.date_of_birth


def update_address(client: Client, data: AddressInformationUpdate) -> None:
    for field, value in data.model_dump().items():
        setattr(client, field, value)


def update_contact(client: Client, data: ContactInformationUpdate) -> None:
    client.phone = data.phone

