from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator
from app.security.schemas import Input
from app.core.catalogs import normalize, names, EntityOut  # noqa: F401 - compatibility exports

Evidence = Literal["confirmed", "inferred", "unverified"]
Activity = Literal["research", "proposal", "implementation", "deployment", "test", "documentation", "incident"]


class ReferenceInput(Input):
    source_type: str = Field(min_length=1, max_length=100)
    source_reference: str = Field(min_length=1, max_length=2048)
    source_date: date | None = None
    external_id: str | None = Field(default=None, max_length=240)
    evidence_level: Evidence = "unverified"


class EntryInput(Input):
    occurred_on: date
    title: str = Field(min_length=1, max_length=240)
    content_markdown: str = Field(default="", max_length=65536)
    summary: str = Field(default="", max_length=4000)
    source: str = Field(default="manual", min_length=1, max_length=100)
    evidence_level: Evidence = "unverified"
    activity_type: Activity = "documentation"
    technologies: list[str] = Field(default_factory=list, max_length=30)
    projects: list[str] = Field(default_factory=list, max_length=30)
    tags: list[str] = Field(default_factory=list, max_length=30)
    related_entry_ids: list[UUID] = Field(default_factory=list, max_length=30)
    references: list[ReferenceInput] = Field(default_factory=list, max_length=30)

    @field_validator("title", "source")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Cannot be blank")
        return value.strip()

    @field_validator("projects", "tags", "technologies")
    @classmethod
    def validate_names(cls, value):
        return names(value)


class EntryUpdate(EntryInput):
    version: int = Field(ge=1)


class VersionInput(Input):
    version: int = Field(ge=1)


class DeleteInput(VersionInput):
    confirm: Literal[True]


class EntryOut(Input):
    id: UUID
    occurred_on: date
    title: str
    content_markdown: str
    summary: str
    status: Literal["draft", "published"]
    source: str
    evidence_level: Evidence
    activity_type: Activity
    technologies: list[str]
    projects: list[EntityOut]
    tags: list[EntityOut]
    related_entry_ids: list[UUID]
    references: list[ReferenceInput]
    version: int
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None


class EntryPage(Input):
    items: list[EntryOut]
    total: int
    offset: int
    limit: int


class ActivitySummary(Input):
    entries: int
    days: int


class FrequentEntity(Input):
    name: str
    count: int


class JournalSummary(Input):
    today: date
    today_entries: list[EntryOut]
    recent_entries: list[EntryOut]
    pending_review: int
    week: ActivitySummary
    month: ActivitySummary
    projects: list[FrequentEntity]
    tags: list[FrequentEntity]
