import os
import re
import uuid
from pathlib import Path
from fastapi import UploadFile
from app.core.config import settings
from app.services.storage_provider import storage_provider

SUPPORTED_CATEGORIES = {
    "Personal",
    "Income",
    "Employment",
    "Investments",
    "Deductions",
    "Credits",
    "Foreign_Information",
    "Dependents",
    "Other",
}


def generate_uuid() -> str:
    """Generate standard UUID v4 string."""
    return str(uuid.uuid4())


def sanitize_filename(filename: str) -> str:
    """Sanitize original filename to remove hazardous characters."""
    filename = os.path.basename(filename)
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", filename)


def ensure_filing_storage_dirs(client_id: str, tax_year: int, filing_id: str) -> dict[str, Path]:
    """
    Ensure target directory layout exists:
    storage/client_documents/{CLI}/{YEAR}/{FILING}/{CATEGORY}/
    """
    base_dir = Path(settings.CLIENT_DOCUMENTS_DIR) / str(client_id) / str(tax_year) / str(filing_id)
    category_paths = {}

    for cat in SUPPORTED_CATEGORIES:
        cat_path = base_dir / cat
        cat_path.mkdir(parents=True, exist_ok=True)
        category_paths[cat] = cat_path

    return category_paths


def save_document_file(
    file: UploadFile,
    client_id: str,
    tax_year: int,
    filing_id: str,
    category: str,
    version: int = 1,
    document_name: str | None = None,
) -> tuple[str, str, str, int]:
    """
    Saves an uploaded file to disk into storage/client_documents/{CLI}/{YEAR}/{FILING}/{CATEGORY}/.
    Returns: (stored_filename, relative_file_path, mime_type, file_size)
    """
    if category not in SUPPORTED_CATEGORIES:
        category = "Other"

    dirs = ensure_filing_storage_dirs(client_id, tax_year, filing_id)
    target_dir = dirs[category]

    original_filename = file.filename or "uploaded_document"
    sanitized = sanitize_filename(original_filename)
    stem, ext = os.path.splitext(sanitized)

    file_uuid = generate_uuid()[:8]
    doc_slug = sanitize_filename(document_name) if document_name else stem
    stored_filename = f"{doc_slug}_v{version}_{file_uuid}{ext.lower()}"

    destination = target_dir / stored_filename

    # Read content length
    file_bytes = file.file.read()
    file_size = len(file_bytes)
    file.file.seek(0)

    # Save via storage provider interface
    storage_key = str(destination)
    relative_path = storage_provider.save(file.file, storage_key, content_type=file.content_type)

    mime_type = file.content_type or "application/octet-stream"

    return stored_filename, relative_path, mime_type, file_size


def save_profile_picture(file: UploadFile, user_id: str, role_type: str) -> str:
    """
    Saves an uploaded profile picture into storage/{user_id}/avatar/{filename}.
    Returns the web URL path e.g. /storage/{user_id}/avatar/{filename}
    """
    ext = os.path.splitext(file.filename or ".jpg")[1].lower()
    if ext not in [".jpg", ".jpeg", ".png", ".webp", ".gif"]:
        ext = ".jpg"

    avatar_dir = Path("storage") / str(user_id) / "avatar"
    avatar_dir.mkdir(parents=True, exist_ok=True)

    filename = f"avatar_{generate_uuid()[:8]}{ext}"
    file_path = avatar_dir / filename

    with open(file_path, "wb") as buffer:
        buffer.write(file.file.read())

    return f"/storage/{user_id}/avatar/{filename}"
