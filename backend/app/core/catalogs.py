"""Shared per-instance catalog validation and race-safe lookup/creation."""
import unicodedata
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.security.schemas import Input


def normalize(value):
    return unicodedata.normalize("NFKC", value).strip().casefold()


def names(values):
    result = {}
    for value in values:
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 120 or len(normalize(value)) > 120:
            raise ValueError("Names must be between 1 and 120 characters")
        result.setdefault(normalize(value), value.strip())
    return list(result.values())


def entity(db, model, name):
    normalized = normalize(name)
    if not normalized or len(normalized) > 120:
        raise HTTPException(422, "El nombre normalizado debe tener entre 1 y 120 caracteres.")
    statement = insert(model).values(name=name.strip(), normalized_name=normalized)
    statement = statement.on_conflict_do_nothing(index_elements=[model.normalized_name])
    db.execute(statement)
    return db.scalar(select(model).where(model.normalized_name == normalized))


class EntityOut(Input):
    id: UUID
    name: str
