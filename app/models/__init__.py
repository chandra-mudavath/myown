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
from app.models.filing_document import DocumentComment, FilingDocument  # noqa: F401
from app.models.lookups import (  # noqa: F401
    AccountTypeLookup,
    CaseStage,
    ChatQueryTopic,
    DocumentCategory,
    DocumentType,
    RequiredDocument,
    StaffRoleLookup,
    TaxType,
    TaxYear,
)
from app.models.user_profile import UserProfile  # noqa: F401
from app.models.hr import HR  # noqa: F401
from app.models.staff_records import (  # noqa: F401
    StaffDocument,
    StaffEmploymentDetails,
    StaffRoleAssignment,
    StaffSalaryHistory,
)
from app.models.case_workflow import Acknowledgment, CaseAssignment, CaseStageHistory, IrsSubmission  # noqa: F401
from app.models.billing import CaseService, Invoice, Payment, Service  # noqa: F401
from app.models.system import AuditLog, Notification  # noqa: F401
from app.models.id_sequence import IdSequence  # noqa: F401
from app.models.chat import ChatAttachment, ChatMessage, ChatParticipant, ChatThread  # noqa: F401

# Registers the before_insert hooks that fill in readable numbers (CLI-…, FLI_…, DOC-…).
import app.services.numbering  # noqa: F401,E402
