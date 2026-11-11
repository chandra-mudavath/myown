# Client Document Storage & Upload Implementation Plan

## 1. Overview & Objective
Currently, clients can start a new tax filing in the client dashboard, but document upload functionality is not yet enabled. 
This plan outlines the architecture, configuration, database models, backend API routes, media directory hierarchy, version control mechanism, and UI enhancements required to enable document uploads and versioning for client filings.

The documents will be stored in a structured media directory under `storage/client_documents/` following the layout specified below.

---

## 2. Media Directory Layout & Hierarchy

All client documents will be organized systematically using client and filing object UUIDs:
`storage/client_documents/{CLI}/{YEAR}/{FILING}/{CATEGORY}/`

Where:
- `{CLI}`: Client Account / Client UUID (e.g. `c7f3b891-2a4e-4e89-b3a1-9f2d1e0a8c2f`)
- `{YEAR}`: Tax Year (e.g. `2025`)
- `{FILING}`: Tax Filing Object UUID (e.g. `e1a4f890-5b3c-4d2a-8f1e-3a7b9c0d2e4f`)
- `{CATEGORY}`: Category Name (e.g., `Personal`, `Income`, `Deductions`, etc.)

To handle re-uploads when staff flags a document as incorrect or requests an updated copy, files are saved with UUID-based versioning identifiers (e.g., `W2_2025_v1_8f9a2b1c.pdf`, `W2_2025_v2_3e4f5a6b.pdf`):

```
storage/
└── client_documents/
    ├── c7f3b891-2a4e-4e89-b3a1-9f2d1e0a8c2f/  <-- {CLI}: Client UUID
    │   └── 2025/                             <-- {YEAR}: Tax Year
    │       └── e1a4f890-5b3c-4d2a-8f1e-3a7b/ <-- {FILING}: Filing Object UUID
    │           ├── Personal/                 <-- {CATEGORY}: Category Name
    │           │   ├── Passport_v1_8f9a2b1c.pdf
    │           │   └── Passport_v2_3e4f5a6b.pdf   <-- (Re-uploaded after staff request)
    │           ├── Income/
    │           │   ├── W2_CompanyA_v1_1a2b3c4d.pdf <-- (Supports multiple files per field/category)
    │           │   ├── W2_CompanyB_v1_4e5f6g7h.pdf
    │           │   └── 1099_MISC_v1_9z8y7x6w.pdf
    │           ├── Employment/
    │           ├── Investments/
    │           ├── Deductions/
    │           ├── Credits/
    │           ├── Foreign_Information/
    │           ├── Dependents/
    │           └── Other/
    │
    └── d8a2c109-1f3b-4c8d-9e2a-7f6b5a4c3d2e/
        └── 2025/
            └── f2b5e901-6c4d-3e2f-1a8b-9c0d1e2f3a4b/
                ├── Personal/
                ├── Income/
                └── Other/
```

### Supported Categories & Multi-File Upload Rules:
Each category / field accepts **multiple files** to be uploaded (e.g., multiple W-2s, multiple 1099s, multiple property tax receipts, etc.):

1. `Personal` (ID verification, SSN card, passport - supports multiple IDs)
2. `Income` (W-2s, 1099-NEC, 1099-MISC, self-employment income - supports multiple wage statements)
3. `Employment` (Employer statements, employment contracts, offer letters)
4. `Investments` (1099-B, crypto exchange statements, stock dividend reports)
5. `Deductions` (Mortgage interest 1098, medical bills, charitable receipts - supports multiple receipts)
6. `Credits` (Education 1098-T, child care provider receipts)
7. `Foreign_Information` (FBAR statements, FATCA forms, foreign income records)
8. `Dependents` (Birth certificates, school enrollment proof per dependent)
9. `Other` (Misc receipts, text notes, additional tax forms)

---

## 3. Configuration Setup

### 3.1 `app/core/config.py`
Add storage configuration settings to Pydantic `Settings`:
- `STORAGE_DIR: str = "storage"`
- `CLIENT_DOCUMENTS_DIR: str = "storage/client_documents"`
- `MAX_UPLOAD_SIZE_MB: int = 10`
- `ALLOWED_EXTENSIONS: list[str] = [".pdf", ".png", ".jpg", ".jpeg", ".csv", ".docx", ".xlsx", ".txt"]`

### 3.2 Environment Files (`.env` and `.env.example`)
Environment setup must be performed as the first step when starting implementation.
Ensure storage configuration keys are added to `.env` (around line 55) and `.env.example`:
```env
# ─── File Storage & Client Media ──────────────────────────────
STORAGE_DIR="storage"
CLIENT_DOCUMENTS_DIR="storage/client_documents"
MAX_UPLOAD_SIZE_MB=10
```

---

## 4. Analysis of Tax Filing Form & Database Schema Design

### 4.1 Form Section Mapping Analysis
An analysis of `app/templates/client/new_filing.html` and `app/models/tax_filing.py` reveals the following form structure and corresponding document requirements:

| Form Section | Form Fields / Context | Associated Category | Supported File Types |
|---|---|---|---|
| **01. Basic Information** | First/Last Name, Email, Filing Type | `Personal` | ID, Passport, SSN |
| **02. Personal & Filing Details** | Residency, Citizenship, Address | `Personal`, `Foreign_Information` | Green Card, Visa, FBAR |
| **03. Spouse Information** | Spouse Details (Joint/Separate) | `Personal` | Spouse ID / SSN card |
| **04. Dependents** | Dependent List & Relationships | `Dependents` | Birth Certificate, School Record |
| **05. Income Categories** | Checkboxes: W-2, 1099, Self-Employment, Dividends | `Income`, `Employment`, `Investments` | W-2s, 1099s, K-1s (multiple) |
| **06. Deductions & Credits** | Mortgage, Medical, Student Loan, Childcare | `Deductions`, `Credits` | Form 1098, 1098-T, Receipts |
| **07. Previous Tax Information**| Prior Year Returns, IRS Notices | `Other` | Form 1040, IRS letters |
| **08. Special Situations** | Crypto, Foreign Bank Accounts, Disasters | `Foreign_Information`, `Investments`, `Other` | Crypto statements, FBAR, .txt notes |

### 4.2 Update `TaxFiling` Model (`app/models/tax_filing.py`)
- Ensure `filing_number` column (e.g. `TAX-2025-000001`) is present or auto-generated upon filing creation.
- Add relationship `documents = relationship("FilingDocument", back_populates="filing", cascade="all, delete-orphan")`.

### 4.3 New Model `FilingDocument` (`app/models/filing_document.py` / `tax_filing.py`)
Create `filing_documents` table designed to support multi-file uploads per category, complete version control, and staff re-upload requests:

- `id`: String UUID (Primary Key - generated using standard UUID v4)
- `filing_id`: String UUID (Foreign Key → `tax_filings.id`)
- `document_group_id`: String UUID (Groups version history for a specific document, e.g. W-2 from Company A)
- `category`: String (e.g., `Personal`, `Income`, `Employment`, `Investments`, `Deductions`, `Credits`, `Foreign_Information`, `Dependents`, `Other`)
- `document_name`: String (Human-readable document title e.g. "Company A W-2 Form")
- `original_filename`: String
- `stored_filename`: String (UUID-versioned file name on disk)
- `file_path`: String (relative path under `storage/client_documents/{CLI}/{YEAR}/{FILING}/{CATEGORY}/`)
- `file_size`: Integer (bytes)
- `mime_type`: String
- `version`: Integer (Starts at `1`, increments on re-upload)
- `is_latest`: Boolean (`True` for active/latest version, `False` for superseded versions)
- `status`: String (`pending_review`, `accepted`, `rejected_reupload_requested`, `superseded`)
- `rejection_reason`: String (Text explanation supplied by staff if re-upload is requested)
- `uploaded_by_type`: String (`client` or `staff`)
- `uploaded_by_id`: String UUID (Auth account ID)
- `uploaded_at`: DateTime (UTC)
- `reviewed_at`: DateTime (UTC, optional)
- `reviewed_by_id`: String UUID (Staff account ID, optional)

### 4.4 Alembic Database Migration
- Generate migration script `add_filing_documents_and_versioning.py` to create `filing_documents` table with UUID keys, versioning columns, and `filing_number` on `tax_filings`.

---

## 5. Storage Helper Service & UUID Management (`app/services/storage_service.py`)

Implement utility functions to manage directory structures, file versioning, multi-file uploads, and UUID identifiers:
1. `generate_uuid() -> str`: Standard UUID v4 generation for primary keys and document version groups.
2. `format_client_number(client_number: str) -> str`: Formats client number consistently as `CLI_0000001`.
3. `format_filing_number(tax_year: int, sequence_id: int) -> str`: Formats filing code as `TAX-2025-000001`.
4. `ensure_filing_storage_dirs(client_id: str, tax_year: int, filing_id: str) -> dict[str, Path]`: Auto-creates all 9 category folders under `storage/client_documents/{client_id}/{tax_year}/{filing_id}/{category}/`.
5. `save_document_version(file, client_id, tax_year, filing_id, category, version, group_uuid) -> tuple[str, str, str, int]`: 
   - Generates a unique file storage name incorporating document title, version (`v1`, `v2`), and UUID.
   - Saves file to disk under the `{category}` subfolder and returns UUID file identifier, relative path, stored filename, and size.

---

## 6. Backend API Routes (`app/api/client/client.py` & Staff Routes)

Expose client and staff document management endpoints using UUID route parameters and supporting multi-file uploads:

1. `POST /client/filings/{filing_uuid}/upload`
   - Accepts multipart form data with multiple files (`files: list[UploadFile]`), `category`, `document_name`, and optional `document_group_id` (for re-uploads/new versions).
   - If `document_group_id` is provided (re-uploading a rejected document):
     - Computes `new_version = current_max_version + 1`.
     - Marks previous versions as `is_latest = False` and status as `superseded`.
     - Creates new `FilingDocument` record with `version = new_version`, `is_latest = True`, and `status = 'pending_review'`.
   - Saves file in target category directory `storage/client_documents/{CLI}/{YEAR}/{FILING}/{CATEGORY}/`.

2. `GET /client/filings/{filing_uuid}/documents`
   - Returns list of uploaded documents grouped by category (including multiple files per category).
   - Includes version history (`version`, `status`, `rejection_reason`, `is_latest`).

3. `GET /client/filings/{filing_uuid}/documents/{doc_uuid}/download`
   - Streams file securely by document UUID to authorized client or assigned staff.

4. `POST /staff/filings/{filing_uuid}/documents/{doc_uuid}/request-reupload`
   - Staff endpoint to reject a document and request a re-upload.
   - Sets status to `rejected_reupload_requested` and records `rejection_reason` / staff notes.
   - Triggers notification / status badge in client portal.

5. `DELETE /client/filings/{filing_uuid}/documents/{doc_uuid}`
   - Allows deletion of pending documents using document UUID.

---

## 7. Frontend UI Enhancements & Upload Progress (Client & Staff Portal)

1. **New Filing Form (`app/templates/client/new_filing.html`)**:
   - Add Document Upload accordion section allowing clients to attach multiple files per category before or after submitting basic info.

2. **Client Portal & Re-upload Workflow (`app/templates/client/dashboard/year-selected.html`)**:
   - Multi-file dropzone / file picker per category.
   - Displays status badges for each document (`Pending Review`, `Accepted`, `Re-upload Requested`).
   - If a document is flagged by staff as incorrect (`Re-upload Requested`), shows staff's rejection reason and a direct "Re-upload Correct Document" button.
   - Version history modal/drawer allowing users and staff to view previous versions (`v1`, `v2`).

3. **Frontend Upload Progress Bar (`app/static/js/new-filing.js` / `dashboard.js`)**:
   - Implements real-time upload progress bar and transfer counter using `XMLHttpRequest.upload.onprogress` / Fetch streams.
   - Displays real-time progress per file (e.g. `45% uploaded (2.1 MB / 4.7 MB)`) with visual animated progress bar.
   - Provides success toasts, error highlights, and upload cancellation options.

---

## 8. Verification & Execution Steps

1. Start implementation by adding storage environment keys to `.env` (around line 55) and `.env.example`.
2. Verify configuration values loaded from `app/core/config.py` (including `.txt` support).
3. Apply database migration for `filing_documents` schema (with UUIDs, form section mapping, and versioning fields).
4. Verify creation of directory structures `storage/client_documents/{CLI}/{YEAR}/{FILING}/{CATEGORY}/`.
5. Test multi-file upload across categories and verify real-time frontend upload progress bar.
6. Test initial document upload (`v1`) and staff document review / rejection flow.
7. Test client re-upload flow (`v2`), verifying version increments, previous file marked `superseded`, and latest file active.
8. Verify document downloads using UUID identifiers and security checks.
