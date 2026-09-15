from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    APP_NAME: str = "UrTax"
    APP_ENV: str = "development"
    DEBUG: bool = True
    SECRET_KEY: str
    ALLOWED_HOSTS: str = "localhost"

    # JWT
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Database
    DATABASE_URL: str

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"

    # Upload & Storage
    UPLOAD_DIR: str = "uploads"
    STORAGE_DIR: str = "storage"
    CLIENT_DOCUMENTS_DIR: str = "storage/client_documents"
    MAX_UPLOAD_SIZE_MB: int = 10
    ALLOWED_EXTENSIONS: list[str] = [".pdf", ".png", ".jpg", ".jpeg", ".csv", ".docx", ".xlsx", ".txt"]


settings = Settings()
