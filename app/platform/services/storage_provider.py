"""Where uploaded files are kept: client documents, chat attachments, payment proofs and staff documents.

STORAGE_BACKEND picks the place: "local" (this server's disk, for development) or "r2" (the private
Cloudflare R2 bucket, for production). Either way every file is encrypted before it is stored
(app/core/crypto.py) and decrypted on the way out, and the database keeps the same kind of key, e.g.
"storage/client_documents/<client>/<year>/<filing>/<category>/<file>". Profile pictures are public and stay
on disk (storage_service.save_profile_picture).
"""
import io
from abc import ABC, abstractmethod
from typing import BinaryIO, Optional
from pathlib import Path

from app.core.config import settings
from app.core.crypto import decrypt_bytes, encrypt_bytes


class BaseStorageProvider(ABC):
    """Abstract interface for storage providers."""

    @abstractmethod
    def save(self, file_obj: BinaryIO, storage_key: str, content_type: Optional[str] = None) -> str:
        """
        Saves file_obj bytes to storage under storage_key.
        Returns the persistent file path / URI / object key.
        """
        pass

    @abstractmethod
    def open(self, storage_key: str) -> BinaryIO:
        """The file's original (decrypted) bytes. Raises FileNotFoundError when it isn't there."""
        pass

    @abstractmethod
    def delete(self, storage_key: str) -> bool:
        """Deletes file/object at storage_key."""
        pass


class LocalStorageProvider(BaseStorageProvider):
    """Local disk filesystem implementation."""

    def save(self, file_obj: BinaryIO, storage_key: str, content_type: Optional[str] = None) -> str:
        destination = Path(storage_key)
        destination.parent.mkdir(parents=True, exist_ok=True)

        # Encrypted at rest (app/core/crypto.py); open() decrypts on the way out
        destination.write_bytes(encrypt_bytes(file_obj.read()))

        try:
            return str(destination.resolve().relative_to(Path.cwd().resolve())).replace("\\", "/")
        except ValueError:
            return str(destination).replace("\\", "/")

    def open(self, storage_key: str) -> BinaryIO:
        path = Path(storage_key)
        if not path.is_file():
            raise FileNotFoundError(storage_key)
        return io.BytesIO(decrypt_bytes(path.read_bytes()))

    def delete(self, storage_key: str) -> bool:
        p = Path(storage_key)
        if p.exists():
            p.unlink()
            return True
        return False


_MISSING = {"404", "NoSuchKey", "NotFound"}


class R2StorageProvider(BaseStorageProvider):
    """A private Cloudflare R2 bucket, through its S3-compatible API (boto3). The object key is the same
    relative path the local provider uses, so files can be copied between the two without database changes."""

    def __init__(self, endpoint: str, bucket: str, access_key_id: str, secret_access_key: str, client=None):
        self.endpoint, self.bucket = endpoint, bucket
        self._key_id, self._secret = access_key_id, secret_access_key
        self._client = client  # tests pass a stand-in

    @property
    def client(self):
        if self._client is None:
            import boto3  # only needed when STORAGE_BACKEND=r2
            from botocore.config import Config

            # R2 has a single "auto" region; checksums only when an operation needs them (R2 doesn't support every one)
            self._client = boto3.client(
                "s3", endpoint_url=self.endpoint, region_name="auto",
                aws_access_key_id=self._key_id, aws_secret_access_key=self._secret,
                config=Config(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"},
                              request_checksum_calculation="when_required",
                              response_checksum_validation="when_required"))
        return self._client

    @staticmethod
    def key(storage_key: str) -> str:
        return storage_key.replace("\\", "/").removeprefix("./").lstrip("/")

    def save(self, file_obj: BinaryIO, storage_key: str, content_type: Optional[str] = None) -> str:
        key = self.key(storage_key)
        # The stored bytes are encrypted, so the object is opaque; the real type is set when sending (app/core/files.py)
        self.client.put_object(Bucket=self.bucket, Key=key, Body=encrypt_bytes(file_obj.read()),
                               ContentType="application/octet-stream")
        return key

    def open(self, storage_key: str) -> BinaryIO:
        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=self.key(storage_key))
        except Exception as exc:
            if getattr(exc, "response", {}).get("Error", {}).get("Code") in _MISSING:
                raise FileNotFoundError(storage_key) from exc
            raise
        return io.BytesIO(decrypt_bytes(obj["Body"].read()))

    def delete(self, storage_key: str) -> bool:
        self.client.delete_object(Bucket=self.bucket, Key=self.key(storage_key))
        return True


def build_storage_provider() -> BaseStorageProvider:
    if settings.STORAGE_BACKEND.lower() == "r2":
        return R2StorageProvider(settings.R2_ENDPOINT, settings.R2_BUCKET,
                                 settings.R2_ACCESS_KEY_ID, settings.R2_SECRET_ACCESS_KEY)
    return LocalStorageProvider()


storage_provider: BaseStorageProvider = build_storage_provider()
