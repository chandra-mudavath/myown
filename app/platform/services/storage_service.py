import io
import os
import re
import uuid
from pathlib import Path
from fastapi import HTTPException, UploadFile, status
from app.core.config import settings
from app.core.errors import AppError
from app.core.files import mime_for
from app.platform.services.storage_provider import storage_provider

# Categories come from the document_categories table; on disk each one is a folder, so only
# plain names are allowed (no path separators or dots).
_SAFE_CATEGORY = re.compile(r"^[A-Za-z0-9_]{1,50}$")

AVATAR_MAX_MB = 5
# Avatars must really be images: the file's first bytes, not just its name, decide.
_IMAGE_SIGNATURES = {b"\x89PNG\r\n\x1a\n": ".png", b"\xff\xd8\xff": ".jpg", b"GIF87a": ".gif", b"GIF89a": ".gif"}


def read_upload(file: UploadFile, max_mb: int | None = None) -> bytes:
    """The uploaded bytes, refusing anything over the size limit (default MAX_UPLOAD_SIZE_MB)."""
    limit = (max_mb or settings.MAX_UPLOAD_SIZE_MB) * 1024 * 1024
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise AppError("SYS-008", f"{file.filename or 'The file'} is larger than {max_mb or settings.MAX_UPLOAD_SIZE_MB} MB.",
                       max_mb=max_mb or settings.MAX_UPLOAD_SIZE_MB)
    return data


def _image_ext(data: bytes) -> str | None:
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return next((ext for sig, ext in _IMAGE_SIGNATURES.items() if data.startswith(sig)), None)


def generate_uuid() -> str:
    """Generate standard UUID v4 string."""
    return str(uuid.uuid4())


def sanitize_filename(filename: str) -> str:
    """Sanitize original filename to remove hazardous characters."""
    filename = os.path.basename(filename)
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", filename)


def ensure_filing_storage_dir(client_id: str, tax_year: int, filing_id: str, category: str) -> Path:
    """
    Ensure the target directory exists:
    storage/client_documents/{CLI}/{YEAR}/{FILING}/{CATEGORY}/
    """
    path = Path(settings.CLIENT_DOCUMENTS_DIR) / str(client_id) / str(tax_year) / str(filing_id) / category
    path.mkdir(parents=True, exist_ok=True)
    return path


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
    if not _SAFE_CATEGORY.match(category or ""):
        category = "Other"

    original_filename = file.filename or "uploaded_document"
    ext = os.path.splitext(original_filename)[1].lower()
    if ext not in settings.ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"File extension {ext} not allowed. Supported: {', '.join(settings.ALLOWED_EXTENSIONS)}")
    file_bytes = read_upload(file)

    target_dir = ensure_filing_storage_dir(client_id, tax_year, filing_id, category)

    sanitized = sanitize_filename(original_filename)
    stem, ext = os.path.splitext(sanitized)

    file_uuid = generate_uuid()[:8]
    doc_slug = sanitize_filename(document_name) if document_name else stem
    stored_filename = f"{doc_slug}_v{version}_{file_uuid}{ext.lower()}"

    destination = target_dir / stored_filename

    file_size = len(file_bytes)

    # Save via storage provider interface. The stored type comes from the extension, not the browser.
    mime_type = mime_for(stored_filename)
    storage_key = str(destination)
    relative_path = storage_provider.save(io.BytesIO(file_bytes), storage_key, content_type=mime_type)

    return stored_filename, relative_path, mime_type, file_size


def save_profile_picture(file: UploadFile, user_id: str, role_type: str) -> str:
    """
    Saves an uploaded profile picture into storage/{user_id}/avatar/{filename}.
    Returns the web URL path e.g. /storage/{user_id}/avatar/{filename}
    """
    data = read_upload(file, AVATAR_MAX_MB)
    ext = _image_ext(data)
    if not ext:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The profile picture must be a PNG, JPG, GIF or WebP image.")

    avatar_dir = Path("storage") / str(user_id) / "avatar"
    avatar_dir.mkdir(parents=True, exist_ok=True)

    filename = f"avatar_{generate_uuid()[:8]}{ext}"
    file_path = avatar_dir / filename

    file_path.write_bytes(data)

    return f"/storage/{user_id}/avatar/{filename}"
