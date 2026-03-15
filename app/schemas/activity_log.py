from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ActivityLogActor(BaseModel):
    id: int
    name: str

    model_config = ConfigDict(from_attributes=True)


class ActivityLogResponse(BaseModel):
    id: int
    project_id: int
    task_id: int | None
    action: str
    old_value: str | None
    new_value: str | None
    actor: ActivityLogActor | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
