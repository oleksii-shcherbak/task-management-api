from datetime import datetime

from pydantic import BaseModel


class AttachmentResponse(BaseModel):
    id: int
    task_id: int
    uploader_id: int | None
    filename: str
    mime_type: str
    size_bytes: int
    created_at: datetime
    url: str
