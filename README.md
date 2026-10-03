# UrTax

Professional tax services web application.

## Tech Stack

| Layer | Technology | Version |
|---|---|---|
| Language | Python | 3.14.2 |
| Web framework | FastAPI | 0.115.6 |
| ASGI server | Uvicorn | 0.32.1 |
| Templates | Jinja2 | 3.1.5 |
| CSS | Tailwind CSS CDN | v3 |
| JavaScript | Vanilla JS (ES6+) | — |
| ORM | SQLAlchemy | 2.0.36 |
| Migrations | Alembic | 1.14.0 |
| Database | MySQL (Dev) / PostgreSQL (Prod) | 8.0+ / 16+ |
| DB driver | PyMySQL (Dev) / psycopg3 (Prod) | 1.1+ / 3.2+ |
| Auth | PyJWT + pwdlib (Argon2) | 2.10.1 / 0.2.1 |
| Task queue | Celery | 5.4.0 |
| Message broker | Redis | 5.2.1 (client) |
| Data processing | Pandas + NumPy | 2.2.3 / 2.2.1 |
| Config | Pydantic Settings | 2.7.0 |
| Testing | Pytest + pytest-asyncio | 8.3.4 / 0.24.0 |
| Linting | Ruff | 0.9.0 |
| Web server | Nginx (prod) | 1.26+ |
| Deployment | Ansible | 10+ |
| OS | Linux (Ubuntu 22.04 LTS) | — |

## Project Structure

```
myown/
├── app/
│   ├── core/           ← config, database, security, deps, template loader
│   ├── platform/       ← Code used by more than one role
│   │   ├── api/        ← auth, chat routes
│   │   ├── models/     ← auth, tax filings, documents, workflow, billing, lookups, chat, ...
│   │   ├── schemas/    ← Pydantic request/response schemas
│   │   ├── services/   ← Business logic (auth, storage, numbering, chat, document review, ...)
│   │   ├── tasks/      ← Background jobs
│   │   ├── templates/  ← base.html, auth/, errors/, chat/, shared partials/
│   │   └── static/     ← Shared CSS/JS/images, served at /static/
│   ├── modules/        ← One folder per role, each with the same layout
│   │   ├── client/
│   │   │   ├── api/        ← Route handlers
│   │   │   ├── models/     ← SQLAlchemy ORM models
│   │   │   ├── schemas/
│   │   │   ├── services/   ← Business logic
│   │   │   ├── templates/client/
│   │   │   └── static/     ← css/, js/ — served at /static/client/
│   │   ├── staff/      ← same layout, static at /static/staff/
│   │   ├── admin/      ← same layout, static at /static/admin/
│   │   └── hr/         ← same layout (placeholder), static at /static/hr/
│   ├── public/         ← Marketing site: landing, about, services, refer, terms
│   │   ├── api/  templates/  static/   ← static at /static/public/
│   ├── db_models.py    ← Imports every model so Base.metadata/Alembic see all tables
│   └── main.py         ← FastAPI app entry point
├── alembic/            ← DB migrations
├── tests/              ← Pytest test suite
├── docs/               ← Architecture docs
├── ansible/            ← Deployment playbooks
├── .env                ← Local secrets (never commit)
├── .env.example        ← Template for .env
├── alembic.ini
├── requirements.txt
└── requirements-dev.txt
```

## Quick Start

```bash
# 1. Create virtual environment
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # Linux/Mac

# 2. Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# 3. Configure environment
copy .env.example .env         # then edit DATABASE_URL, SECRET_KEY

# 4. Run database migrations
alembic upgrade head

# 5. Start the server
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000

## Commands Reference

```bash
# Run server
uvicorn app.main:app --reload

# Create migration
alembic revision --autogenerate -m "add users table"

# Apply migrations
alembic upgrade head

# Run tests
pytest tests/ -v

# Lint
ruff check app/
```