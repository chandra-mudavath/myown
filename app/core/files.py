"""Sending stored files (documents, payment proofs, chat attachments) to the browser."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from urllib.parse import quote

from fastapi.responses import Response

from app.core.crypto import decrypt_bytes
from app.core.errors import AppError

log = logging.getLogger(__name__)

# The type a stored file is sent with comes from its extension, never from what the uploader's
# browser claimed: a ".txt" sent as "text/html" would otherwise run as a web page on our site.
FILE_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".txt": "text/plain; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
# Only these open inline (preview); everything else always downloads.
INLINE_TYPES = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".txt"}


def _ext(name: str | None) -> str:
    return os.path.splitext(name or "")[1].lower()


def mime_for(filename: str | None) -> str:
    """The type to store and send for a file with this name."""
    return FILE_TYPES.get(_ext(filename), "application/octet-stream")


def send_file(path: str | None, filename: str | None = None, media_type: str | None = None,
              inline: bool = False) -> Response:
    """The file as a download (or inline preview). A record whose file is gone from disk shows the
    "This file isn't available" screen instead of crashing, and is logged so an admin can follow up.

    media_type is ignored: the type always comes from the stored file's extension (see FILE_TYPES)."""
    if not path or not Path(path).is_file():
        log.warning("Stored file missing on disk: %s (%s)", path, filename)
        raise AppError("DOC-009", "File not found.", name=filename or "This file")
    ext = _ext(path)
    disposition = "inline" if inline and ext in INLINE_TYPES else "attachment"
    name = filename or Path(path).name
    quoted = quote(name)
    disposition += f"; filename*=utf-8''{quoted}" if quoted != name else f'; filename="{name}"'
    # Stored files are encrypted at rest (app/core/crypto.py): decrypt, then send the original bytes
    return Response(decrypt_bytes(Path(path).read_bytes()), media_type=mime_for(path),
                    headers={"Content-Disposition": disposition, "X-Content-Type-Options": "nosniff",
                             "Cache-Control": "private, no-store"})
