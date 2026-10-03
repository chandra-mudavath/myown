from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    APP_NAME: str = "UrTax"
    APP_ENV: str = "development"
    DEBUG: bool = True
    SECRET_KEY: str = "dev-secret-key-please-change-in-production-32bytes"
    ALLOWED_HOSTS: str = "localhost"

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

    # Upload & Storage
    UPLOAD_DIR: str = "uploads"
    STORAGE_DIR: str = "storage"
    CLIENT_DOCUMENTS_DIR: str = "storage/client_documents"
    MAX_UPLOAD_SIZE_MB: int = 10
    ALLOWED_EXTENSIONS: list[str] = [".pdf", ".png", ".jpg", ".jpeg", ".csv", ".docx", ".xlsx", ".txt"]

    # Chat
    CHAT_HIDE_AFTER_DAYS: int = 90  # threads are hidden (kept for audit) this long after they close
    CHAT_ATTACHMENTS_DIR: str = "private_storage/chat_attachments"  # not under the public /storage mount


settings = Settings()
