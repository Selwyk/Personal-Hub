import hashlib
import json
from datetime import date

from pydantic import Field, field_validator
from app.security.schemas import Input
from app.modules.journal.schemas import Evidence, Activity, ReferenceInput, names


class CandidateInput(Input):
    occurred_on: date
    title: str = Field(min_length=1, max_length=240)
    summary: str = Field(default="", max_length=4000)
    detail_markdown: str = Field(default="", max_length=65536)
    activity_type: Activity
    projects: list[str] = Field(default_factory=list, max_length=30)
    tags: list[str] = Field(default_factory=list, max_length=30)
    technologies: list[str] = Field(default_factory=list, max_length=30)
    source_type: str = Field(min_length=1, max_length=100)
    source_reference: str = Field(min_length=1, max_length=2048)
    source_date: date | None = None
    external_id: str | None = Field(default=None, min_length=1, max_length=240)
    evidence_level: Evidence
    content_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    collector_version: str = Field(min_length=1, max_length=100)
    raw_metadata: dict = Field(default_factory=dict)

    @field_validator("raw_metadata")
    @classmethod
    def validate_provenance(cls, value):
        refs = value.get("source_references")
        if refs is not None:
            if not isinstance(refs, list) or not 1 <= len(refs) <= 30:
                raise ValueError("source_references must contain 1 to 30 references")
            for ref in refs:
                ReferenceInput.model_validate(ref)
        return value

    @field_validator("title", "source_type", "source_reference", "collector_version")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Cannot be blank")
        return value.strip()

    @field_validator("projects", "tags", "technologies")
    @classmethod
    def validate_names(cls, value):
        return names(value)


class BatchInput(Input):
    import_key: str = Field(min_length=1, max_length=240)
    candidates: list[dict] = Field(min_length=1, max_length=100)


class ReviewInput(Input):
    version: int = Field(ge=1)
    candidate: CandidateInput


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode()).hexdigest()


def content_hash(candidate):
    return canonical_hash(candidate.model_dump(mode="json", exclude={"content_hash", "external_id",
        "collector_version", "raw_metadata", "source_type", "source_reference", "source_date"}))


def batch_schema():
    schema = BatchInput.model_json_schema()
    candidate = CandidateInput.model_json_schema()
    schema.setdefault("$defs", {}).update(candidate.pop("$defs", {}))
    schema["properties"]["candidates"]["items"] = candidate
    return schema
