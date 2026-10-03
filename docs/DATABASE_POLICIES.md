# UrTax: Database access policies and indexing

Status: **adopted 2026-10-02.** Replaces §5 (RLS) and §6 (indexing) of
[DATABASE_IMPLEMENTATION_PLAN.md](DATABASE_IMPLEMENTATION_PLAN.md), which were written for revision 1 and for
PostgreSQL only. Table names here follow [Final-Data-Model.md](../Data%20Models/Final-Data-Model.md).

Local development runs on **MySQL 8**; production runs on **PostgreSQL**. Everything below works on both, and
says so where they differ.

---

## 1. How access is enforced: three layers

| Layer | Where | MySQL (local) | PostgreSQL (prod) | Job |
|---|---|---|---|---|
| 1. App policy | service / query code | ✅ | ✅ | **The source of truth.** Every rule in §3 is checked here, so local and production behave the same. |
| 2. Database users | `GRANT` per table | ✅ | ✅ | Backstop for "never delete" and "never change history", even if app code has a bug. |
| 3. Row level security | `CREATE POLICY` | ❌ not available | ✅ later phase | Backstop for "a client only sees their own rows", inside the database. |

Layer 1 must never rely on layers 2 or 3: MySQL has no row level security, so a check missing in code would only
show up in production.

---

## 2. Local MySQL vs production PostgreSQL

| Topic | MySQL 8 (local) | PostgreSQL (prod) | Rule in this codebase |
|---|---|---|---|
| Text comparison | case-insensitive (default collation) | case-sensitive | Normalise before saving and comparing: emails lowercased (done in `auth_service`, `admin`), readable numbers uppercased (`FLI_`, `CLI-`). Never rely on `==` ignoring case. |
| `ILIKE '%term%'` search | rendered as `lower(x) LIKE lower(y)`, full scan | trigram index (`pg_trgm`) | Fine locally; fast in production. |
| Partial indexes (`WHERE ...`) | not supported | supported | Created by migrations on PostgreSQL only (`uq_chat_threads_case`); the rule is also checked in code. |
| Row level security | not supported | supported | See §6. |
| Timestamps | `DATETIME`, timezone dropped | `TIMESTAMPTZ` | Always write UTC (`datetime.now(timezone.utc)`); treat values read back as UTC. |
| DDL in transactions | no (a failed migration leaves partial changes) | yes | Migrations check what already exists before creating, so a re-run finishes the job. |
| `NULL` sort order | `NULL` first ascending | `NULL` last ascending | Don't depend on it; add an explicit order when `NULL`s matter. |
| CHECK constraints | enforced (8.0.16+) | enforced | Same constraints on both. |

**Test on PostgreSQL before every deploy.** Run `alembic upgrade head` and the test suite against a PostgreSQL
database (a staging database or a local Docker container), not only against local MySQL.

---

## 3. Policy per table

Legend: **S** select · **I** insert · **U** update · **D** delete. *own* = rows of the signed-in client (through
`clients.account_id` → `tax_filings.client_id`). *System* = the app acting without a signed-in user (sign-up,
login, numbering, background jobs). **D: never** means rows are retired with a flag (`is_active`, `status`,
`left_at`, `hidden_at`, `deleted_at`) and kept.

HR is a later phase; its column records the agreed intent.

### Lookups
`account_types`, `staff_roles`, `tax_years`, `tax_types`, `document_categories`, `document_types`,
`case_stages`, `chat_query_topics`, `required_documents`, `services`

| Client | Staff | Admin | HR | System | Delete |
|---|---|---|---|---|---|
| S active rows | S | S I U | S | — | never: set `is_active = false` |

`id_sequences`: numbering service only (System U). No screen reads it.

### Identity and login

| Table | Client | Staff | Admin | HR | System | Delete |
|---|---|---|---|---|---|---|
| `auth_accounts` | S U own (password, email through their flows) | S U own | S all; I staff / admin / client; U `is_active`, lock | S staff accounts | I on sign-up; U login counters | never: `is_active = false` |
| `auth_sessions`, `auth_refresh_tokens`, `auth_password_reset_tokens`, `auth_email_verification_tokens` | — | — | — | — | S I U D | **allowed** (expired tokens) |
| `auth_login_attempts` | — | — | S (security review) | — | I | never (append-only) |
| `user_profiles` | S U own | S U own; S clients on cases, S colleagues' names | S U all | S U staff / admin / HR | I on sign-up | never |
| `clients` | S own | S | S I U | — | I on sign-up | never |
| `staff` | — | S own; S colleagues' names | S I U | S U `job_title` | — | never |
| `admins` | — | — | S I U | — | — | never |
| `hr` | — | — | S I U | S own | — | never |

### Staff records

| Table | Staff | Admin | HR | Delete |
|---|---|---|---|---|
| `staff_role_assignments` | S own | S I U D | S | **allowed**: revoking a role removes the row; write `audit_logs` |
| `staff_employment_details` | S own (open question) | S I U | S I U | never |
| `staff_documents` | S own (open question) | S | S I U | never (upload a replacement) |
| `staff_salary_history` | S own (open question) | S I | S I | never; **append-only**, no U |

### Tax case

| Table | Client | Staff | Admin | Delete |
|---|---|---|---|---|
| `tax_filings` | S own; I own; U own intake fields while the case is at `pending_review`, `info_pending` or `docs_pending` | S all ("All Cases"); U stage and working fields | S I U | never: moves through stages |
| `case_assignments` | — | S | S I U (end with `status = 'ended'`) | never |
| `case_stage_history` | — | S I | S I | never; **append-only** |
| `irs_submissions`, `acknowledgments` | S own (status only) | S I U | S I U | never |

### Documents

| Table | Client | Staff | Admin | Delete |
|---|---|---|---|---|
| `filing_documents` | S own; I own (upload); U only through re-upload (marks the old version `superseded`) | S I U review fields (`status`, `rejection_reason`, `reviewed_*`) | S I U | never: new version instead |
| `document_comments` | S I own (reply) | S I | S I | never; **append-only** |

### Billing

| Table | Client | Staff | Admin | Delete |
|---|---|---|---|---|
| `case_services` | S own | S I U | S I U | never |
| `invoices` | S own | S | S I U (void with `status = 'void'`) | never |
| `payments` | S own | S | S U (refund status) | never; I by System from the payment provider |

### System

| Table | Everyone | Admin | System | Delete |
|---|---|---|---|---|
| `audit_logs` | — | S | I | never; **append-only** |
| `notifications` | S U own (`is_read`, `read_at`) | S own | I | never |

### Messaging (rules from the messaging plan, 2026-10-02)

| Table | Client | Staff / Admin / HR (internal) | System | Delete |
|---|---|---|---|---|
| `chat_threads` | S own CASE / QUERY threads (not hidden); I QUERY | S threads they are in (not hidden); I DIRECT / GROUP; U group name (owners, admins), query status / owner; admins S hidden threads (audit view) | I CASE; U `last_message_*`, `hide_after`, `hidden_at` | never: `hidden_at` |
| `chat_participants` | S members of own threads; U own `last_read_at`, `is_muted` | same; I U by group owners, admins, assignment sync | I U | never: `left_at` |
| `chat_messages` | S I in own threads, `visibility = 'all'` only | S I in their threads (incl. `internal` notes); U own message (edit 15 min, soft delete) | I system messages | never: `deleted_at` |
| `chat_attachments` | S I with their message | S I; U `filing_document_id` (save to case documents) | — | never |

HR has no access to CASE / QUERY threads (`account_types.has_case_access = false`). Hidden threads are read only by
admins (audit view) and backend developers (`urtax_readonly`).

---

## 4. Layer 1: enforcing the policies in code

- **Read through scoped queries.** Client routes never load a record by id alone. They go through a helper that
  adds the ownership filter, for example `client_filing(db, account, filing_id)` returning `None` for someone
  else's case. The client routes already follow this (`TaxFiling.client_id == client.id` in
  `app/modules/client/api/client.py`); new code collects these helpers in one module instead of repeating the filter.
- **One check per action.** Writes go through a service function that checks the matrix before changing anything
  (`chat_service.can_post(...)`, `document_review.add_comment(...)`). Routes don't write models directly.
- **No `db.delete()` outside the tables marked "allowed".** Retire rows with their flag instead. Today the app has
  no deletes at all.
- **Tests for denial.** For each client-owned table, a test that client A gets nothing (or 404) for client B's id.

## 5. Layer 2: database users and grants (MySQL and PostgreSQL)

Three users, generated by [`db_grants.py`](../db_grants.py) from the models, so the list stays complete:

```
python db_grants.py mysql --database myown         # local
python db_grants.py postgresql --database <prod>  # production
```

| User | Used by | Permissions |
|---|---|---|
| `urtax_migrate` | Alembic (`MIGRATION_DATABASE_URL`) | owns the tables; create / alter / drop |
| `urtax_app` | the app (`DATABASE_URL`) | S I U on every table; **no U** on append-only tables; **D** only on login tokens and `staff_role_assignments` |
| `urtax_readonly` | backend developers, audit | S only |

The script prints SQL and runs nothing. Review it, replace `CHANGE_ME` passwords, and run it as the database admin.
On MySQL, re-run it after each migration that adds a table. On PostgreSQL, default privileges cover new tables, and
a re-run applies their special cases.

**Order to switch over:**
1. Run the script for the target database.
2. Set `MIGRATION_DATABASE_URL` to `urtax_migrate`, and run `alembic upgrade head`.
3. Set `DATABASE_URL` to `urtax_app` and restart the app.

`app/main.py` runs `create_all()` on startup. With `urtax_app` it can no longer create tables, so **run migrations
before deploying code that adds a table**. Running `create_all()` only in development is the cleaner fix.

## 6. Layer 3: row level security (PostgreSQL production, later phase)

Add it after layer 1 helpers and denial tests exist, and test it on PostgreSQL before enabling. Outline:

1. The app connects as `urtax_app`, which does **not** own the tables (owners bypass RLS).
2. At the start of each request transaction the app sets who is asking:
   ```python
   # app/core/database.py: SQLAlchemy "after_begin" event, PostgreSQL only
   conn.execute(text("SELECT set_config('app.account_id', :id, true), set_config('app.account_type', :t, true)"),
                {"id": account_id or "", "t": account_type or "SYSTEM"})
   ```
   `true` keeps the values to the current transaction, so nothing leaks between pooled connections. Sign-up,
   login and background jobs run as `SYSTEM`.
3. Policies on the client-owned tables (`tax_filings`, `filing_documents`, `document_comments`, `invoices`,
   `payments`, `case_services`, `irs_submissions`, `chat_*`), for example:
   ```sql
   ALTER TABLE tax_filings ENABLE ROW LEVEL SECURITY;
   CREATE POLICY internal_all ON tax_filings
     USING (current_setting('app.account_type', true) IN ('STAFF', 'ADMIN', 'SYSTEM'));
   CREATE POLICY client_own ON tax_filings
     USING (current_setting('app.account_type', true) = 'CLIENT'
            AND client_id IN (SELECT id FROM clients WHERE account_id = current_setting('app.account_id', true)));
   ```
4. Ship it as a migration that runs on PostgreSQL only (`if bind.dialect.name == 'postgresql'`).

---

## 7. Indexing

Indexes are defined on the models (`__table_args__`) and created by migrations (`b8d0f2a4c6e9_query_indexes.py`
for the query indexes). Each one exists for a query the app runs:

| Table | Index | Query it serves |
|---|---|---|
| `tax_filings` | `(client_id, tax_year, created_at)` | client dashboard / filings for a year, newest first |
| | `(status, created_at)` | staff stage counts and stage filter, admin status filter |
| | `(tax_year, status)` | staff list by year (and stage) |
| | `(created_at)`, `(updated_at)` | newest-first lists, staff feed, admin "recently updated" |
| `filing_documents` | `(filing_id, is_latest, uploaded_at)` | latest documents of a case |
| | `(document_group_id, is_latest)` | version history |
| | `(uploaded_by_type, uploaded_at)` | staff feed and unread count: client uploads |
| `document_comments` | `(filing_id, created_at)`, `(document_group_id, created_at)` | review threads in order |
| | `(author_type, kind, created_at)` | staff feed and unread count: client replies |
| `clients`, `staff` | `(created_at)` | directory lists |
| `auth_refresh_tokens` | `(account_id, revoked_at)` | sign out everywhere |
| `case_assignments` | `(staff_id, status)`, `(case_id, status)` | "My Queue", who is working a case |
| `case_stage_history` | `(case_id, created_at)` | case history |
| `notifications` | `(recipient_account_id, is_read, created_at)` | bell and unread count |
| `audit_logs` | `(entity_type, entity_id)`, `(actor_account_id, created_at)`, `(created_at)` | record history, per-person, by date |
| `invoices` / `payments` | `(status, due_date)` / `(status, paid_at)` | unpaid and overdue, revenue |
| `staff_documents` | `(expiry_date)` | HR: expiring soon |
| `staff_salary_history` | `(staff_id, effective_date)` | salary history |
| `chat_threads` | `(case_id, kind)`, `(kind, status, owner_account_id)`, `(hidden_at, hide_after)`, `direct_key` UK | chats of a case, query queue, hiding job, reuse 1:1 thread |
| `chat_participants` | `(account_id, left_at)`, `(thread_id, account_id)` UK | inbox, membership |
| `chat_messages` | `(thread_id, created_at)` | messages and unread counts |
| PostgreSQL only | trigram on case number, email, names, readable numbers | `ILIKE '%term%'` search boxes |

Every foreign key column is indexed (InnoDB requires it; PostgreSQL gets it from `index=True` or a composite that
starts with that column). Every readable number has a unique index.

**Rules for new queries:**
- A new filter or sort on a table that will grow gets an index in the same change, in the model and in a migration.
- Composite order: equality columns first, then the range or sort column (`client_id, tax_year, created_at`).
- Don't add an index for a column with two or three values on its own (`is_latest`, `status` alone); put it after
  a selective column.
- Check a slow query with `EXPLAIN` on both databases.

**Lag that indexes don't fix:**
- **Lists without paging.** Admin pages load every row: `/admin` dashboard, clients, cases, staff
  (`app/modules/admin/api/admin.py`, `.all()` without `limit`). Fine at today's size; at thousands of rows they need paging
  like the staff case list (`staff.py`, `offset` / `limit`).
- **A query per row (N+1).** Counting or loading inside a loop; use one grouped query instead (fixed on the admin
  clients page).
- **Full-name search.** `first_name || ' ' || last_name ILIKE '%term%'` can't use an index on either database.
  Search the parts separately, or search `user_profiles` once names move there.
