from logging.config import fileConfig
import os

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.core.database import Base

from dotenv import load_dotenv

# Import all models so Alembic can detect them
import app.models  # noqa: F401

config = context.config
load_dotenv()
data_base_url = os.getenv("DATABASE_URL") or settings.DATABASE_URL

if not data_base_url:
    raise RuntimeError("DATABASE_URL is not set")

# Render fix: Convert postgres:// or postgresql:// to postgresql+psycopg://
if data_base_url.startswith("postgres://"):
    data_base_url = data_base_url.replace("postgres://", "postgresql+psycopg://", 1)
elif data_base_url.startswith("postgresql://") and not data_base_url.startswith("postgresql+"):
    data_base_url = data_base_url.replace("postgresql://", "postgresql+psycopg://", 1)

config.set_main_option("sqlalchemy.url", data_base_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
