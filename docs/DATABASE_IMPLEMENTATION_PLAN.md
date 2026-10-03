# UrTax — Database & Access-Control Implementation Plan

Status: **proposal / design phase** — no schema changes have been applied yet.

> **Superseded in part (2026-10-02):** the table catalog is now [Final-Data-Model.md](../Data%20Models/Final-Data-Model.md),
> and §5 (RLS) and §6 (indexing) are replaced by [DATABASE_POLICIES.md](DATABASE_POLICIES.md), which covers local
> MySQL and production PostgreSQL.
Scope: client ↔ staff ↔ HR ↔ admin tax-filing workflow, backed by PostgreSQL, designed for
low-latency dashboards and strict row-level data isolation between roles.

---

## 1. Tech stack fit-check

| Layer | Current | Verdict |
|---|---|---|
| DB | PostgreSQL 16+ | ✅ Native `ENUM`, `JSONB`, partial/composite indexes, **Row Level Security (RLS)**, materialized views — everything below relies on these. |
| ORM/Migrations | SQLAlchemy 2.0 + Alembic | ✅ Keep. Map new Postgres `ENUM`s the same way `AccountType` already is in [auth.py](app/models/auth.py). |
| Sessions | Sync `SessionLocal` in [database.py](app/core/database.py), plain `get_db()` | ⚠️ Needs one addition: a per-request `SET LOCAL app.*` call so RLS policies know who's asking (Section 4). No engine/driver change needed. |
| Auth | PyJWT + Argon2, cookie-based | ✅ Already gives us `account_id` + `account_type` per request — exactly what RLS needs. |
| Background jobs | Celery + Redis (present in `requirements.txt`, but [app/tasks](app/tasks/__init__.py) is empty) | 🟡 Currently unused — this plan puts it to work: stage-change notifications, document virus/parse scans, nightly KPI materialized-view refresh, scheduled salary-hike effective dates. |
| File storage | Local `UPLOAD_DIR` (config only, [config.py](app/core/config.py)) | 🟡 Fine for dev. Recommend an S3-compatible object store before production — schema stores a `file_path`/key either way, so this is a later infra swap, not a schema blocker. |
| Caching | None | 🟡 Recommend Redis (already a dependency) for short-TTL caching of admin KPI aggregates — keeps "no lag" promise without adding new infra. |
| Testing | Pytest present, [tests/](tests/__init__.py) nearly empty | 🟡 Add fixtures that assert cross-tenant RLS denial (e.g. Client A cannot `SELECT` Client B's filings) as part of this rollout. |

No stack changes are required — only additive schema + one session-context hook.

---

## 2. Role model changes

Today `AccountType` = `CLIENT | STAFF | ADMIN` ([auth.py](app/models/auth.py)). Business context adds **HR** as a fourth,
fully separate login, and splits `STAFF` into four working roles.

```python
class AccountType(str, enum.Enum):
    CLIENT = "CLIENT"
    STAFF  = "STAFF"
    HR     = "HR"
    ADMIN  = "ADMIN"

class StaffRole(str, enum.Enum):
    INITIATOR = "INITIATOR"
    PREPARER  = "PREPARER"
    REVIEWER  = "REVIEWER"
    MANAGER   = "MANAGER"
```

`Staff.role: Mapped[StaffRole]` is added to the existing `staff` table (one primary role per staff account,
matching "4 roles under staff"). `HR` gets its own profile table mirroring `Admin`/`Staff` (Section 3.2).

---

## 3. Table catalog

### 3.1 Identity (existing — unchanged)
`auth_accounts`, `auth_sessions`, `auth_refresh_tokens`, `auth_password_reset_tokens`,
`auth_email_verification_tokens`, `auth_login_attempts`, `clients`, `staff`, `admins` — keep as-is,
only add `staff.role` (above).

### 3.2 HR domain (new)

**`hr_profiles`** — HR's own identity, same shape as `admins`/`staff`
- `id`, `hr_number`, `account_id` FK → `auth_accounts` (unique), `first_name`, `last_name`, `phone`, `department`, `created_at`, `updated_at`

**`staff_employment_details`** — the "mandatory details" HR maintains, 1:1 with `staff`
- `id`, `staff_id` FK (unique), `employee_number`, `date_of_joining`, `employment_type` (`FULL_TIME/PART_TIME/CONTRACT/INTERN`), `department`, `designation`, `reporting_manager_staff_id` FK → `staff` (nullable), `employment_status` (`ACTIVE/ON_LEAVE/TERMINATED`), `termination_date`, `current_salary`, `updated_at`

**`staff_documents`** — HR-managed employment docs (offer letter, ID proof, contract) — **distinct from case documents**
- `id`, `staff_id` FK, `doc_type` (`ID_PROOF/OFFER_LETTER/CONTRACT/CERTIFICATION/OTHER`), `file_path`, `uploaded_by_account_id` (HR), `uploaded_at`, `expiry_date` (nullable), `notes`

**`staff_salary_history`** — every hike, append-only
- `id`, `staff_id` FK, `effective_date`, `previous_salary`, `new_salary`, `hike_percentage` (generated column), `reason`, `approved_by_account_id`, `created_at`
- `staff_employment_details.current_salary` is a denormalized cache of the latest row here — updated in the same transaction as the insert, so dashboards never need to aggregate this table to show "current salary" (avoids the "lag" the request calls out).

**`staff_performance_reviews`** *(phase 2, optional)* — qualitative manager reviews
- `id`, `staff_id`, `review_period_start`, `review_period_end`, `reviewer_account_id`, `rating`, `comments`, `created_at`
- Quantitative performance (cases closed, avg handle time) should stay a **derived query** over `tax_filings` / `filing_status_history` — don't duplicate that into a stored table.

### 3.3 Tax filing core (new)

**`tax_filings`** — the case record (`TX-2025-0891` in the staff mockup), one row per client per tax year
| Column | Notes |
|---|---|
| `id` | PK |
| `case_number` | e.g. `TX-2025-0891`, unique |
| `client_id` | FK → `clients` |
| `tax_year` | int |
| `filing_method` | `PAPER_FILING \| FORM_8879`, nullable until decided in Review |
| `stage` | internal pipeline enum, Section 4 |
| `client_status` | denormalized client-facing enum, kept in sync with `stage` in the same transaction |
| `priority` | `LOW/MED/HIGH` |
| `due_date`, `submitted_at`, `closed_at`, `created_at`, `updated_at` | |

`client_status` is intentionally duplicated (not computed on read) so the client dashboard is a single indexed
`WHERE client_id = ? AND tax_year = ?` lookup — no CASE-mapping or join needed per page render.

**`filing_intake`** — the client's submitted form data (1:1 with `tax_filings`)
- `id`, `filing_id` FK (unique), `filing_status_type` (single/married/etc.), `dependents_count`, `notes`, `extra_answers JSONB`
- Structured columns for what staff/admin need to query or report on; `extra_answers JSONB` absorbs the long tail of
  intake questions that change over time **without an Alembic migration each time** — this is the main lever for
  "flexible UI without lag."

**`filing_assignments`** — who is working the case, per role, with history
- `id`, `filing_id` FK, `staff_id` FK, `role` (`INITIATOR/PREPARER/REVIEWER/MANAGER`), `assigned_at`, `unassigned_at`, `is_active`
- A dedicated table (not 4 FK columns on `tax_filings`) because a case is worked by **four different staff
  concurrently** at different stages, and reassignment history matters for audit/performance reporting.

**`filing_documents`** — income documents (W-2, Form 16, 1099-*, bank certificate, …)
- `id`, `filing_id` FK, `doc_type` (`W2/FORM_16/FORM_1099_INT/FORM_1099_MISC/FORM_1099_NEC/BANK_CERTIFICATE/OTHER`), `doc_label` (free text for `OTHER`), `file_path`, `file_size`, `mime_type`, `status` (`REQUESTED/UPLOADED/VERIFIED/REJECTED`), `uploaded_by_account_id`, `verified_by_staff_id`, `uploaded_at`, `verified_at`, `notes`
- Any staff role may `INSERT`/`UPDATE`; **no staff role is ever granted `DELETE`** — enforced at the DB privilege
  level (Section 5.3), not just in the app, per "staff... upload, update, view except delete."

**`filing_status_history`** — append-only audit trail, doubles as the client-facing activity feed
- `id`, `filing_id` FK, `from_stage`, `to_stage`, `changed_by_account_id`, `note`, `visible_to_client` (bool), `client_message` (nullable text), `created_at`
- Admin's "Audit Trail" reads this unfiltered; the client dashboard's "Recent activity" reads
  `WHERE visible_to_client = true` — one table, two views, no duplication.

**`filing_issues`** — staff-internal problem tracking (info pending / docs pending / discrepancies)
- `id`, `filing_id` FK, `issue_type` (`MISSING_DOC/MISSING_INFO/DISCREPANCY`), `reference` (field or doc name), `description`, `status` (`OPEN/RESOLVED`), `raised_by_staff_id`, `raised_at`, `resolved_by_staff_id`, `resolved_at`

**`client_action_items`** — client-facing task list ("Action required" panel)
- `id`, `filing_id` FK, `issue_id` FK (nullable, links back to the staff-side issue), `label`, `action_type` (`UPLOAD_DOC/CONFIRM_PAYMENT/PROVIDE_INFO/SIGN_FORM`), `target_url`, `is_resolved`, `created_at`, `resolved_at`

**`filing_payments`** — payment pending → confirmation payment
- `id`, `filing_id` FK, `amount`, `currency`, `status` (`PENDING/CONFIRMED/FAILED/REFUNDED`), `payment_method`, `transaction_ref`, `requested_at`, `confirmed_at`, `confirmed_by_staff_id`
- Feeds admin revenue KPIs directly (`SUM(amount) WHERE status = 'CONFIRMED'`).

**`filing_efiling_details`** — only populated when `filing_method = FORM_8879`, 1:1 with `tax_filings`
- `id`, `filing_id` FK (unique), `form_8879_signed_at`, `efile_submitted_at`, `efile_accepted_at`, `irs_confirmation_number`, `status` (`SUBMITTED/ACCEPTED/REJECTED`)

**`filing_amendments`** — reopens a `COMPLETED`/`CLOSED` case
- `id`, `filing_id` FK, `reason`, `requested_by_account_id`, `opened_at`, `closed_at`, `status` (`OPEN/RESOLVED`)
- While a row here is open, `tax_filings.client_status = AMENDMENT`; closing it returns the filing to `COMPLETED`.

### 3.4 Cross-cutting (new)

**`notifications`** — generic, reused by client/staff/HR/admin ("client has been notified via portal")
- `id`, `account_id` FK (recipient), `type`, `title`, `body`, `related_filing_id` (nullable), `is_read`, `created_at`

---

## 4. State machines

### 4.1 Internal pipeline stage (`FilingStage`) — staff-facing, drives `tax_filings.stage`
```
SUBMITTED → INFO_PENDING ⇄ DOCS_PENDING → PREPARATION → REVIEW
   → PAYMENT_PENDING → PAYMENT_CONFIRMED
   → [FORM_8879 only] EFILING →  COMPLETED
   → [PAPER_FILING]            →  COMPLETED
   → (either) → AMENDMENT → COMPLETED   (loop, via filing_amendments)
   → CLOSED   (terminal, admin/manager action after COMPLETED)
```
- `INFO_PENDING`/`DOCS_PENDING` are set by the **Initiator** and block progress until the linked `filing_issues`
  row is resolved.
- The `EFILING` stage only exists in the state machine when `tax_filings.filing_method = 'FORM_8879'`; paper
  filings skip straight from `PAYMENT_CONFIRMED` to `COMPLETED`.

### 4.2 Client-facing status (`ClientFilingStatus`) — drives `tax_filings.client_status`
Exactly the 5 states requested — a many-to-one mapping from the internal stage:

| Internal `stage` | Client sees |
|---|---|
| `SUBMITTED` | **Submitted** |
| `INFO_PENDING`, `DOCS_PENDING`, `PREPARATION`, `REVIEW`, `PAYMENT_PENDING`, `PAYMENT_CONFIRMED`, `EFILING` | **In Progress** |
| `COMPLETED` | **Completed** |
| `CLOSED` | **Closed** |
| (open row in `filing_amendments`) | **Amendment** — overrides whatever `stage` says while open |

This mapping lives once in a service function (e.g. `app/services/filing_service.py::to_client_status()`) and is
applied every time `stage` changes, writing the result into `client_status` in the same DB transaction — so client
dashboard reads never need to know about the 10+ internal stages.

---

## 5. Row Level Security (RLS)

The app uses a single pooled DB user for every request ([database.py](app/core/database.py)), so Postgres RLS
needs to know "who is asking" via **session-local settings**, set once per request before any query runs.

### 5.1 Request-scoped session context
Add a small hook in `get_db()` (or a new `app/core/rls.py`) that runs once the account is resolved:

```python
def apply_rls_context(db: Session, account: AuthAccount) -> None:
    db.execute(
        text("SELECT set_config('app.current_account_id', :aid, true), "
             "set_config('app.current_account_type', :atype, true)"),
        {"aid": account.id, "atype": account.account_type.value},
    )
```
Called from a dependency that runs *after* `get_current_account` but *before* the route body, inside the same
transaction — `true` (the `is_local` flag) makes it auto-reset at transaction end, so nothing leaks across pooled
connections.

### 5.2 Policy design (per table)

| Table | Client | Staff (any role) | HR | Admin |
|---|---|---|---|---|
| `tax_filings` | `SELECT/INSERT` where `client_id` matches own `clients.id` | `SELECT` all (needed for "All Cases"); `UPDATE` only stage/assignment columns | *no policy → denied* | full access (`BYPASSRLS`) |
| `filing_documents`, `filing_issues`, `filing_payments`, `client_action_items` | `SELECT` via `filing_id → tax_filings.client_id` match; `INSERT` for uploads only | `SELECT/INSERT/UPDATE` all, **no `DELETE` policy at all** | denied | full access |
| `filing_status_history` | `SELECT` where `visible_to_client = true` and owns the filing | `SELECT/INSERT` all | denied | full access |
| `staff_employment_details`, `staff_documents`, `staff_salary_history`, `hr_profiles` | denied | denied (staff can't see their own HR file via this path — expose via a narrow read-only view instead if needed) | `SELECT/INSERT/UPDATE` all | full access |
| `notifications` | `SELECT/UPDATE(is_read)` where `account_id = current_account_id()` | same, own rows only | same | full access |

Example policy (Postgres DDL, illustrative):
```sql
ALTER TABLE tax_filings ENABLE ROW LEVEL SECURITY;

CREATE POLICY client_own_filings ON tax_filings
  FOR SELECT
  USING (
    current_setting('app.current_account_type', true) = 'CLIENT'
    AND client_id = (SELECT id FROM clients WHERE account_id = current_setting('app.current_account_id', true))
  );

CREATE POLICY staff_read_all_filings ON tax_filings
  FOR SELECT
  USING (current_setting('app.current_account_type', true) = 'STAFF');
```
The admin DB role should be created with `BYPASSRLS` (or the app's single pooled role gets an additional
`USING (current_setting('app.current_account_type', true) = 'ADMIN')` policy on every table) — simplest is
`BYPASSRLS` granted only when `account_type = ADMIN`'s policy check would otherwise require duplicating a
catch-all clause on every table.

### 5.3 `DELETE` lockdown (belt-and-suspenders)
Beyond RLS, `REVOKE DELETE ON filing_documents, filing_issues, filing_payments FROM app_staff_role;` at the
Postgres grant level — this is a stronger guarantee than an RLS policy (RLS only filters *rows*, a missing
`DELETE` policy already denies all deletes by default in Postgres, so this is actually automatic once no
`FOR DELETE` policy is created for staff — documented here so it isn't accidentally added later).

---

## 6. Indexing & performance

- `tax_filings (client_id, tax_year)` — composite, powers the year-picker dashboard directly.
- `tax_filings (assigned via filing_assignments.staff_id, is_active)` — staff "My Queue".
- `filing_status_history (filing_id, created_at DESC)` — activity feed pagination.
- Partial index `filing_issues (filing_id) WHERE status = 'OPEN'` — fast "does this case have open issues" checks.
- `filing_documents (filing_id, doc_type)`.
- Materialized view `mv_admin_kpis` (active cases, closed this month, revenue, avg handle time) refreshed by a
  Celery beat task every few minutes — keeps the admin dashboard's KPI cards O(1) instead of live-aggregating
  across all filings on every page load.

---

## 7. Rollout phases

1. **Phase 1 — core filing loop**: `tax_filings`, `filing_intake`, `filing_assignments`, `filing_documents`,
   `filing_status_history`, `filing_issues`, `client_action_items` + RLS scaffolding (session context hook +
   policies on these tables). This alone lets the year-picker dashboard show real data instead of placeholders.
2. **Phase 2 — payments & filing method branch**: `filing_payments`, `filing_efiling_details`, `filing_amendments`.
3. **Phase 3 — HR**: `AccountType.HR`, `hr_profiles`, `staff_employment_details`, `staff_documents`,
   `staff_salary_history`, plus RLS policies restricting HR to those tables only.
4. **Phase 4 — analytics & notifications**: `notifications`, `mv_admin_kpis` materialized view, Celery tasks
   (stage-change notifications, nightly KPI refresh, salary-hike effective-date scheduling).

## 8. Open questions to confirm before implementing

1. Should `MANAGER` be a `staff` row with `role = MANAGER`, or does a manager also need `STAFF` + elevated
   `ADMIN`-like visibility across all queues? (Plan above treats manager as a `staff` role like the other three.)
2. Any staff-visible view onto their *own* `staff_employment_details`/`staff_documents` (e.g. "my payslip"), or is
   that strictly HR/admin-only per the current wording ("HR can only have rights...")?
3. Confirm the exact document type list beyond `W2/FORM_16/1099-*/BANK_CERTIFICATE` so the `doc_type` enum is
   complete before the first migration (an enum change later is a non-trivial Postgres migration).
4. File storage target for `file_path` (local disk vs S3-compatible) — affects whether we store a path or a
   storage key + bucket.
