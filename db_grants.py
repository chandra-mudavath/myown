"""Print the SQL that creates UrTax's three database users and their table permissions.

    python db_grants.py mysql --database myown
    python db_grants.py postgresql --database urtax

Nothing is executed: review the output, set real passwords, and run it as the database
admin. Re-run it after every migration that adds a table (MySQL has no default privileges,
so new tables need their grants added). See docs/DATABASE_POLICIES.md.

Users:
  urtax_migrate   owns the tables; Alembic only (MIGRATION_DATABASE_URL)
  urtax_app       the running app (DATABASE_URL): read / insert / update, delete only where listed
  urtax_readonly  backend developers and audit (e.g. hidden chats): read only
"""
from __future__ import annotations

import argparse
import sys

from app.core.database import Base
import app.db_models  # noqa: F401  (registers every table on Base.metadata)

# Insert and read only: history that must never change.
APPEND_ONLY = {
    "audit_logs",
    "case_stage_history",
    "staff_salary_history",
    "auth_login_attempts",
    "document_comments",
}

# The only tables the app may DELETE from. Login tokens are short-lived; revoking a staff
# role removes its row (the change is recorded in audit_logs). Everything else is kept and
# retired with a flag (is_active, status, left_at, hidden_at, deleted_at).
DELETE_ALLOWED = {
    "auth_sessions",
    "auth_refresh_tokens",
    "auth_password_reset_tokens",
    "auth_email_verification_tokens",
    "staff_role_assignments",
}

USERS = ("urtax_migrate", "urtax_app", "urtax_readonly")


def app_privileges(table: str) -> str:
    if table in APPEND_ONLY:
        return "SELECT, INSERT"
    if table in DELETE_ALLOWED:
        return "SELECT, INSERT, UPDATE, DELETE"
    return "SELECT, INSERT, UPDATE"


def mysql(database: str, tables: list[str]) -> list[str]:
    host = "'%'"  # narrow to the app server's host in production
    out = [f"-- MySQL: run as root. Database `{database}`."]
    out += [f"CREATE USER IF NOT EXISTS '{u}'@{host} IDENTIFIED BY 'CHANGE_ME';" for u in USERS]
    out.append(f"GRANT ALL PRIVILEGES ON `{database}`.* TO 'urtax_migrate'@{host};")
    out.append(f"GRANT SELECT ON `{database}`.* TO 'urtax_readonly'@{host};")
    for t in tables:
        out.append(f"GRANT {app_privileges(t)} ON `{database}`.`{t}` TO 'urtax_app'@{host};")
    out.append("FLUSH PRIVILEGES;")
    return out


def postgresql(database: str, tables: list[str]) -> list[str]:
    out = [f'-- PostgreSQL: run as a superuser, connected to database "{database}".']
    out += [f"DO $$ BEGIN CREATE ROLE {u} LOGIN PASSWORD 'CHANGE_ME'; "
            f"EXCEPTION WHEN duplicate_object THEN NULL; END $$;" for u in USERS]
    out += [
        f'GRANT CONNECT ON DATABASE "{database}" TO urtax_app, urtax_readonly;',
        "GRANT USAGE, CREATE ON SCHEMA public TO urtax_migrate;",
        "GRANT USAGE ON SCHEMA public TO urtax_app, urtax_readonly;",
        "-- Tables must be owned by urtax_migrate, not by urtax_app: an owner bypasses row level security.",
        "-- If an earlier deploy created them as another user, run once: REASSIGN OWNED BY <old_owner> TO urtax_migrate;",
        "GRANT SELECT ON ALL TABLES IN SCHEMA public TO urtax_readonly;",
        "ALTER DEFAULT PRIVILEGES FOR ROLE urtax_migrate IN SCHEMA public GRANT SELECT ON TABLES TO urtax_readonly;",
        "-- New tables get read / insert / update by default; re-run this script to apply the special cases.",
        "ALTER DEFAULT PRIVILEGES FOR ROLE urtax_migrate IN SCHEMA public GRANT SELECT, INSERT, UPDATE ON TABLES TO urtax_app;",
        "REVOKE ALL ON alembic_version FROM urtax_app;",
    ]
    for t in tables:
        out.append(f"REVOKE ALL ON {t} FROM urtax_app; GRANT {app_privileges(t)} ON {t} TO urtax_app;")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dialect", choices=("mysql", "postgresql"))
    parser.add_argument("--database", required=True, help="database name, e.g. myown")
    args = parser.parse_args()

    tables = sorted(Base.metadata.tables)
    unknown = (APPEND_ONLY | DELETE_ALLOWED) - set(tables)
    if unknown:
        print(f"Tables listed here but not in the models: {sorted(unknown)}", file=sys.stderr)
        return 1
    lines = (mysql if args.dialect == "mysql" else postgresql)(args.database, tables)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
