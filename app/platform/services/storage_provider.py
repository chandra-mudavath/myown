import io
from abc import ABC, abstractmethod
from typing import BinaryIO, Optional
from pathlib import Path

from app.core.crypto import decrypt_bytes, encrypt_bytes


class BaseStorageProvider(ABC):
    """Abstract interface for storage providers (Local, S3, Azure Blob, GCS)."""

    @abstractmethod
    def save(self, file_obj: BinaryIO, storage_key: str, content_type: Optional[str] = None) -> str:
        """
        Saves file_obj bytes to storage under storage_key.
        Returns the persistent file path / URI / object key.
        """
        pass

    @abstractmethod
    def open(self, storage_key: str) -> BinaryIO:
        """Returns a readable binary file handle for storage_key."""
        pass

    @abstractmethod
    def delete(self, storage_key: str) -> bool:
        """Deletes file/object at storage_key."""
        pass


class LocalLocalStorageProvider(BaseStorageProvider):
    """Local disk filesystem implementation."""

    def save(self, file_obj: BinaryIO, storage_key: str, content_type: Optional[str] = None) -> str:
        destination = Path(storage_key)
        destination.parent.mkdir(parents=True, exist_ok=True)

        # Encrypted at rest (app/core/crypto.py); send_file decrypts on the way out
        destination.write_bytes(encrypt_bytes(file_obj.read()))

        try:
            return str(destination.resolve().relative_to(Path.cwd().resolve())).replace("\\", "/")
        except ValueError:
            return str(destination).replace("\\", "/")

    def open(self, storage_key: str) -> BinaryIO:
        """The file's original (decrypted) bytes."""
        return io.BytesIO(decrypt_bytes(Path(storage_key).read_bytes()))

    def delete(self, storage_key: str) -> bool:
        p = Path(storage_key)
        if p.exists():
            p.unlink()
            return True
        return False


# Default provider instance (easily swapped via env / config e.g. STORAGE_BACKEND="s3")
storage_provider: BaseStorageProvider = LocalLocalStorageProvider()
