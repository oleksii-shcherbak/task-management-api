"""Storage service abstraction.

`StorageService` defines the interface; `LocalStorageService` and
`S3StorageService` are the two implementations.  The active backend is
selected by `get_storage_service()` based on `settings.STORAGE_BACKEND`.

Storage paths (e.g. `"attachments/uuid.pdf"`) are internal keys - they are
never returned directly in API responses.  Callers must always convert them to
URLs via `get_url()` before serializing to JSON.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from app.config import settings


class StorageService(ABC):
    @abstractmethod
    async def upload_file(self, data: bytes, filename: str) -> str:
        """Upload file bytes and return the storage path (not a URL)."""

    @abstractmethod
    async def delete_file(self, path: str) -> None:
        """Delete a file by its storage path."""

    @abstractmethod
    def get_url(self, path: str) -> str:
        """Return an accessible URL for the given storage path."""


class LocalStorageService(StorageService):
    def __init__(self, base_dir: str = settings.UPLOAD_DIR) -> None:
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)

    async def upload_file(self, data: bytes, filename: str) -> str:
        path = self._base / filename
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        # Writing to disk is blocking, so run it off the event loop
        await asyncio.to_thread(path.write_bytes, data)
        return filename

    async def delete_file(self, path: str) -> None:
        target = self._base / path
        await asyncio.to_thread(target.unlink, missing_ok=True)

    def get_url(self, path: str) -> str:
        return f"/{settings.UPLOAD_DIR}/{path}"


class S3StorageService(StorageService):
    def __init__(self) -> None:
        self._client = boto3.client(
            "s3",
            region_name=settings.AWS_S3_REGION,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        )
        self._bucket = settings.AWS_S3_BUCKET
        self._expires = settings.AWS_S3_PRESIGNED_URL_EXPIRES

    async def upload_file(self, data: bytes, filename: str) -> str:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=filename,
            Body=data,
        )
        return filename

    async def delete_file(self, path: str) -> None:
        try:
            await asyncio.to_thread(
                self._client.delete_object,
                Bucket=self._bucket,
                Key=path,
            )
        except ClientError:
            pass  # Ignore errors when trying to delete non-existent objects

    def get_url(self, path: str) -> str:
        # Generate a presigned URL for the S3 object
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": path},
            ExpiresIn=self._expires,
        )


def get_storage_service() -> StorageService:
    if settings.STORAGE_BACKEND == "s3":
        return S3StorageService()
    return LocalStorageService()
