# Export all models so Alembic and Base.metadata can discover them.
from app.models.auth import (  # noqa: F401
    AuthAccount,
    AuthEmailVerificationToken,
    AuthLoginAttempt,
    AuthPasswordResetToken,
    AuthRefreshToken,
    AuthSession,
)
from app.models.client import Client  # noqa: F401
from app.models.staff import Staff, StaffRole  # noqa: F401
from app.models.admin import Admin  # noqa: F401
from app.models.tax_filing import TaxFiling  # noqa: F401
from app.models.filing_document import FilingDocument  # noqa: F401
