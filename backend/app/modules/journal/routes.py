import json
from datetime import date, timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Response
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select, func
from sqlalchemy.orm import Session as DbSession

from app.core.config import settings
from app.database.models import Entry, Project, Tag, entry_projects, entry_tags
from app.database.session import get_db
from app.modules.journal.schemas import (EntryInput, EntryUpdate, EntryOut, EntryPage,
                                         VersionInput, DeleteInput, JournalSummary)
from app.modules.journal.service import (create_entry, apply_input, entry_dict, find_entry,
                                        version_check, filtered_query, markdown_export)
from app.security.service import signed_in, now

router = APIRouter(dependencies=[Depends(signed_in)], tags=["journal"])


@router.post("/journal/entries", response_model=EntryOut, status_code=201)
def create(body: EntryInput, db: DbSession = Depends(get_db, scope="function")):
    return entry_dict(db, create_entry(db, body))


@router.get("/journal/entries", response_model=EntryPage)
def listing(q: str | None = Query(default=None, max_length=500), occurred_on: date | None = None,
            date_from: date | None = None, date_to: date | None = None,
            month: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
            status: Literal["draft", "published"] | None = None,
            project_id: UUID | None = None, tag_id: UUID | None = None,
            sort: Literal["date", "relevance"] = "date", offset: int = Query(default=0, ge=0),
            limit: int = Query(default=50, ge=1, le=200), db: DbSession = Depends(get_db, scope="function")):
    query = filtered_query(q, occurred_on, date_from, date_to, month, status, project_id, tag_id, sort)
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    return {"items": [entry_dict(db, entry) for entry in db.scalars(query.offset(offset).limit(limit))],
            "total": total, "offset": offset, "limit": limit}


@router.get("/journal/entries/{entry_id}", response_model=EntryOut)
def detail(entry_id: UUID, db: DbSession = Depends(get_db, scope="function")):
    return entry_dict(db, find_entry(db, entry_id))


@router.put("/journal/entries/{entry_id}", response_model=EntryOut)
def edit(entry_id: UUID, body: EntryUpdate, db: DbSession = Depends(get_db, scope="function")):
    entry = find_entry(db, entry_id, lock=True)
    version_check(entry, body.version)
    apply_input(db, entry, body)
    entry.version += 1
    db.flush()
    return entry_dict(db, entry)


@router.post("/journal/entries/{entry_id}/publish", response_model=EntryOut)
def publish(entry_id: UUID, body: VersionInput, db: DbSession = Depends(get_db, scope="function")):
    entry = find_entry(db, entry_id, lock=True)
    version_check(entry, body.version)
    if entry.status != "published":
        entry.status = "published"
        entry.published_at = entry.updated_at = now()
        entry.version += 1
    db.flush()
    return entry_dict(db, entry)


@router.post("/journal/entries/{entry_id}/unpublish", response_model=EntryOut)
def unpublish(entry_id: UUID, body: VersionInput, db: DbSession = Depends(get_db, scope="function")):
    entry = find_entry(db, entry_id, lock=True)
    version_check(entry, body.version)
    if entry.status == "published":
        entry.status = "draft"
        entry.published_at = None
        entry.updated_at = now()
        entry.version += 1
    db.flush()
    return entry_dict(db, entry)


@router.delete("/journal/entries/{entry_id}")
def remove(entry_id: UUID, body: DeleteInput, db: DbSession = Depends(get_db, scope="function")):
    entry = find_entry(db, entry_id, lock=True)
    version_check(entry, body.version)
    db.delete(entry)
    return {"ok": True}


@router.get("/journal/export")
def export(format: Literal["json", "markdown"] = "json", entry_id: UUID | None = None,
           month: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
           db: DbSession = Depends(get_db, scope="function")):
    query = filtered_query(month=month)
    if entry_id:
        find_entry(db, entry_id)
        query = query.where(Entry.id == entry_id)
    items = [entry_dict(db, row) for row in db.scalars(query)]
    contents = json.dumps({"schema_version": 1, "entries": jsonable_encoder(items)},
                          ensure_ascii=False, indent=2) if format == "json" else markdown_export(items)
    suffix = "json" if format == "json" else "md"
    return Response(contents, media_type="application/json" if format == "json" else "text/markdown",
                    headers={"Content-Disposition": f'attachment; filename="journal.{suffix}"'})


@router.get("/journal/calendar")
def calendar_view(month: str = Query(pattern=r"^\d{4}-\d{2}$"), db: DbSession = Depends(get_db, scope="function")):
    query = filtered_query(month=month).order_by(None).subquery()
    return {"days": [{"date": day, "count": count} for day, count in db.execute(
        select(query.c.occurred_on, func.count()).group_by(query.c.occurred_on)
        .order_by(query.c.occurred_on))]}


class JournalWidgetProvider:
    id = "journal"
    version = 1

    def summary(self, db):
        from app.database.models import Candidate
        today = now().astimezone(ZoneInfo(settings().app_timezone)).date()
        week = today - timedelta(days=today.weekday())
        month = today.replace(day=1)
        def activity(start):
            count, days = db.execute(select(func.count(Entry.id), func.count(func.distinct(Entry.occurred_on)))
                .where(Entry.status == "published", Entry.occurred_on.between(start, today))).one()
            return {"entries": count, "days": days}
        def frequent(model, association, id_col):
            return [{"name": name, "count": count} for name, count in db.execute(
                select(model.name, func.count()).join(association, model.id == id_col)
                .join(Entry, Entry.id == association.c.entry_id).where(Entry.status == "published")
                .group_by(model.id).order_by(func.count().desc(), model.name).limit(6))]
        return JournalSummary.model_validate({"today": today, "today_entries": [entry_dict(db, row) for row in db.scalars(
                    filtered_query(occurred_on=today))],
                "pending_review": db.scalar(select(func.count()).select_from(Candidate)
                                              .where(Candidate.status == "pending_review")),
                "recent_entries": [entry_dict(db, row) for row in db.scalars(filtered_query().limit(5))],
                "week": activity(week), "month": activity(month),
                "projects": frequent(Project, entry_projects, entry_projects.c.project_id),
                "tags": frequent(Tag, entry_tags, entry_tags.c.tag_id)})
