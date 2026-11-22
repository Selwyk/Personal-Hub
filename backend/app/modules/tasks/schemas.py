from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.core.catalogs import EntityOut, names
from app.security.schemas import Input

TaskStatus = Literal["todo", "in_progress", "completed"]
TaskPriority = Literal["low", "normal", "high", "urgent"]


class TaskInput(Input):
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=65536)
    status: TaskStatus = "todo"
    priority: TaskPriority = "normal"
    due_on: date | None = None
    projects: list[str] = Field(default_factory=list, max_length=30)
    tags: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("title")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Cannot be blank")
        return value.strip()

    @field_validator("projects", "tags")
    @classmethod
    def validate_names(cls, value):
        return names(value)


class TaskVersion(Input):
    version: int = Field(ge=1)


class TaskUpdate(TaskInput):
    version: int = Field(ge=1)


class TaskOut(Input):
    id: UUID
    title: str
    description: str
    status: TaskStatus
    priority: TaskPriority
    due_on: date | None
    parent_id: UUID | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    version: int
    projects: list[EntityOut]
    tags: list[EntityOut]


class TaskPage(Input):
    items: list[TaskOut]
    total: int
    offset: int
    limit: int


class TaskExport(Input):
    schema_version: Literal[1] = 1
    tasks: list[TaskOut]
