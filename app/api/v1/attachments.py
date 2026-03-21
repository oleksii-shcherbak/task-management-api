from __future__ import annotations

import uuid
from pathlib import Path

import filetype
from fastapi import APIRouter, Depends, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, get_member_or_403
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.core.storage import StorageService, get_storage_service
from app.database import get_db
from app.models.attachment import Attachment
from app.models.project_member import ProjectRole
from app.models.task import Task
from app.models.user import User
from app.schemas.attachment import AttachmentResponse

task_attachments_router = APIRouter(prefix="/tasks", tags=["attachments"])
attachments_router = APIRouter(prefix="/attachments", tags=["attachments"])

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024

# Office formats are ZIP internally - filetype returns "application/zip" for all of them.
_OFFICE_EXTENSION_MIME: dict[str, str] = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

# Text-based formats have no magic bytes - filetype returns None for them.
_TEXT_EXTENSION_MIME: dict[str, str] = {
    ".svg": "image/svg+xml",
    ".txt": "text/plain",
    ".csv": "text/csv",
}

ALLOWED_MIME_TYPES: set[str] = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/svg+xml",
    "application/pdf",
    "application/zip",
    "application/gzip",
    "text/plain",
    "text/csv",
} | set(_OFFICE_EXTENSION_MIME.values())


def _detect_mime(data: bytes, filename: str) -> str | None:
    """Detect MIME type from magic bytes, with extension fallback for formats filetype
    can't distinguish (SVG, plain text, Office docs stored as ZIP).
    Returns None if the format is unrecognised."""
    ext = Path(filename).suffix.lower()
    kind = filetype.guess(data)

    if kind is not None:
        if kind.mime == "application/zip" and ext in _OFFICE_EXTENSION_MIME:
            return _OFFICE_EXTENSION_MIME[ext]
        return kind.mime

    return _TEXT_EXTENSION_MIME.get(ext)


def _to_response(attachment: Attachment, storage: StorageService) -> AttachmentResponse:
    return AttachmentResponse(
        id=attachment.id,
        task_id=attachment.task_id,
        uploader_id=attachment.uploader_id,
        filename=attachment.filename,
        mime_type=attachment.mime_type,
        size_bytes=attachment.size_bytes,
        created_at=attachment.created_at,
        url=storage.get_url(attachment.storage_path),
    )


async def _get_attachment_or_404(attachment_id: int, db: AsyncSession) -> Attachment:
    result = await db.execute(
        select(Attachment)
        .options(selectinload(Attachment.task))
        .where(Attachment.id == attachment_id)
    )
    attachment = result.scalar_one_or_none()
    if attachment is None:
        raise NotFoundError("Attachment not found")
    return attachment


@task_attachments_router.post(
    "/{task_id}/attachments",
    response_model=AttachmentResponse,
    status_code=201,
)
async def upload_attachment(
    task_id: int,
    file: UploadFile,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
) -> AttachmentResponse:
    result = await db.execute(
        select(Task).where(Task.id == task_id, Task.deleted_at.is_(None))
    )
    task = result.scalar_one_or_none()
    if task is None:
        raise NotFoundError("Task not found")
    await get_member_or_403(task.project_id, current_user.id, db)

    data = await file.read()
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise ValidationError("File exceeds the 10 MB limit")

    mime_type = _detect_mime(data, file.filename or "")
    if mime_type is None or mime_type not in ALLOWED_MIME_TYPES:
        raise ValidationError("File type not allowed")

    ext = Path(file.filename or "").suffix.lower()
    storage_path = f"attachments/{uuid.uuid4()}{ext}"
    await storage.upload_file(data, storage_path)

    attachment = Attachment(
        task_id=task_id,
        uploader_id=current_user.id,
        filename=file.filename or storage_path,
        storage_path=storage_path,
        mime_type=mime_type,
        size_bytes=len(data),
    )
    db.add(attachment)
    await db.commit()
    await db.refresh(attachment)

    return _to_response(attachment, storage)


@task_attachments_router.get(
    "/{task_id}/attachments",
    response_model=list[AttachmentResponse],
)
async def list_attachments(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
) -> list[AttachmentResponse]:
    result = await db.execute(
        select(Task).where(Task.id == task_id, Task.deleted_at.is_(None))
    )
    task = result.scalar_one_or_none()
    if task is None:
        raise NotFoundError("Task not found")
    await get_member_or_403(task.project_id, current_user.id, db)

    result = await db.execute(
        select(Attachment)
        .where(Attachment.task_id == task_id)
        .order_by(Attachment.created_at.asc())
    )
    attachments = list(result.scalars().all())

    return [_to_response(a, storage) for a in attachments]


@attachments_router.get("/{attachment_id}/url", response_model=AttachmentResponse)
async def get_attachment_url(
    attachment_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
) -> AttachmentResponse:
    attachment = await _get_attachment_or_404(attachment_id, db)
    await get_member_or_403(attachment.task.project_id, current_user.id, db)
    return _to_response(attachment, storage)


@attachments_router.delete("/{attachment_id}", status_code=204)
async def delete_attachment(
    attachment_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
) -> None:
    attachment = await _get_attachment_or_404(attachment_id, db)
    member = await get_member_or_403(attachment.task.project_id, current_user.id, db)

    if attachment.uploader_id != current_user.id and member.role not in (
        ProjectRole.OWNER,
        ProjectRole.MANAGER,
    ):
        raise ForbiddenError(
            "Only the uploader or project owners/managers can delete attachments"
        )

    storage_path = attachment.storage_path
    await db.delete(attachment)
    await db.commit()

    # Storage delete happens after the DB record is removed - if it fails,
    # the file becomes orphaned but data consistency is preserved.
    await storage.delete_file(storage_path)
