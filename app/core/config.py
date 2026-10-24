from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_DEV_SECRET_KEY = "dev-secret-key-please-change-in-production-32bytes"


def _valid_fernet_keys(value: str) -> bool:
    from cryptography.fernet import Fernet

    keys = [k.strip() for k in value.split(",") if k.strip()]
    try:
        return bool(keys) and all(Fernet(k.encode()) for k in keys)
    except (ValueError, TypeError):
        return False


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    APP_NAME: str = "CovrentTax"
    APP_ENV: str = "development"
    DEBUG: bool = True
    SECRET_KEY: str = _DEV_SECRET_KEY
    ALLOWED_HOSTS: str = "localhost,127.0.0.1"  # production: the site's host names, e.g. "covrenttax.com,www.covrenttax.com"
    # false = only admins create staff / HR accounts (Admin → Quick actions); public sign-up with a
    # @covrenttax.com address is refused. Admins still sign up only through the admin link from Settings.
    STAFF_SELF_SIGNUP: bool = False
    MAINTENANCE_MODE: bool = False  # true = everyone except signed-in admins sees the "We'll be right back" page

    # Encryption at rest for bank / FBAR numbers, SSN/TIN and e-file PINs (see app/core/crypto.py).
    # Comma-separated Fernet keys; the first encrypts, all decrypt. Required in production. Back it up in Bitwarden.
    DATA_ENCRYPTION_KEYS: str = ""

    # JWT
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Database (MySQL local dev fallback; PostgreSQL in production via .env)
    DATABASE_URL: str = "mysql+pymysql://root:password@localhost:3306/myowntax"

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def assemble_db_connection(cls, v: str) -> str:
        if isinstance(v, str):
            if v.startswith("postgres://"):
                return v.replace("postgres://", "postgresql+psycopg://", 1)
            elif v.startswith("postgresql://") and not v.startswith("postgresql+"):
                return v.replace("postgresql://", "postgresql+psycopg://", 1)
        return v
    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_TASK_ALWAYS_EAGER: bool = True  # true = run tasks in the web process (no Redis/worker needed)

    # Upload & Storage
    UPLOAD_DIR: str = "uploads"
    STORAGE_DIR: str = "storage"
    CLIENT_DOCUMENTS_DIR: str = "storage/client_documents"
    MAX_UPLOAD_SIZE_MB: int = 30
    ALLOWED_EXTENSIONS: list[str] = [".pdf", ".png", ".jpg", ".jpeg", ".csv", ".docx", ".xlsx", ".txt"]

    # Email: "console" writes each message to the server log instead of sending it (development);
    # "smtp" sends through SMTP_HOST, e.g. Resend (smtp.resend.com) or Google Workspace (smtp.gmail.com).
    EMAIL_BACKEND: str = "console"       # console | smtp
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587                 # 587 = STARTTLS, 465 = SSL
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    EMAIL_FROM: str = "no-reply@covrenttax.com"
    EMAIL_FROM_NAME: str = "CovrentTax"
    EMAIL_REPLY_TO: str = "support@covrenttax.com"  # where a client's reply to an app email goes

    # Payments: where clients send manual payments. Blank values hide that option's details.
    PAYMENT_UPI_ID: str = ""             # e.g. "covrenttax@icici"
    PAYMENT_UPI_PAYEE: str = ""          # name shown in the UPI app
    PAYMENT_BANK_DETAILS: str = ""       # wire / ACH instructions, one item per line (a quoted multi-line value in .env)
    PAYMENT_PROOFS_DIR: str = "private_storage/payment_proofs"  # not under the public /storage mount
    # PayPal Checkout (main online option). Blank client id / secret hides it.
    PAYPAL_MODE: str = "sandbox"         # sandbox | live
    PAYPAL_CLIENT_ID: str = ""
    PAYPAL_CLIENT_SECRET: str = ""
    PUBLIC_BASE_URL: str = ""            # e.g. "https://app.covrenttax.com"; blank uses the request's own host

    # Chat
    CHAT_HIDE_AFTER_DAYS: int = 90  # threads are hidden (kept for audit) this long after they close
    CHAT_ATTACHMENTS_DIR: str = "private_storage/chat_attachments"  # not under the public /storage mount

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.lower() == "production"

    @property
    def allowed_hosts(self) -> list[str]:
        return [h.strip() for h in self.ALLOWED_HOSTS.split(",") if h.strip()]

    @model_validator(mode="after")
    def production_is_locked_down(self) -> "Settings":
        """Refuse to start in production with development defaults (forgeable sign-ins, debug pages, unsafe links)."""
        if not self.is_production:
            return self
        problems = []
        if self.DEBUG:
            problems.append("DEBUG must be false")
        if self.SECRET_KEY == _DEV_SECRET_KEY or self.SECRET_KEY.startswith("CHANGE-ME") or len(self.SECRET_KEY) < 32:
            problems.append("SECRET_KEY must be a random value of 32+ characters (openssl rand -hex 32)")
        if not self.PUBLIC_BASE_URL.startswith("https://"):
            problems.append("PUBLIC_BASE_URL must be the site's https:// address")
        if not self.allowed_hosts or "*" in self.allowed_hosts:
            problems.append("ALLOWED_HOSTS must list the site's host names")
        if not _valid_fernet_keys(self.DATA_ENCRYPTION_KEYS):
            problems.append("DATA_ENCRYPTION_KEYS must hold at least one Fernet key (see app/core/crypto.py)")
        if problems:
            raise ValueError("Unsafe production settings: " + "; ".join(problems))
        return self


settings = Settings()
