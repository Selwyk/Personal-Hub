import calendar
from datetime import date

from fastapi import HTTPException
from sqlalchemy import select, delete, func, or_

from app.database.models import Entry, Project, Tag, SourceReference, EntryRelation
from app.core.catalogs import entity  # noqa: F401 - compatibility export
from app.security.service import now


def entry_dict(db, entry):
    return {"id": str(entry.id), "occurred_on": entry.occurred_on,
            "title": entry.title, "content_markdown": entry.content_markdown,
            "summary": entry.summary, "status": entry.status, "source": entry.source,
            "evidence_level": entry.evidence_level, "activity_type": entry.activity_type,
            "technologies": entry.technologies, "version": entry.version,
            "created_at": entry.created_at, "updated_at": entry.updated_at,
            "published_at": entry.published_at,
            "projects": [{"id": str(x.id), "name": x.name} for x in entry.projects],
            "tags": [{"id": str(x.id), "name": x.name} for x in entry.tags],
            "related_entry_ids": list(db.scalars(select(EntryRelation.related_id).where(
                EntryRelation.entry_id == entry.id))),
            "references": [{"source_type": x.source_type, "source_reference": x.source_reference,
                "source_date": x.source_date, "external_id": x.external_id,
                "evidence_level": x.evidence_level} for x in entry.references]}


def find_entry(db, entry_id, lock=False):
    query = select(Entry).where(Entry.id == entry_id)
    if lock:
        query = query.with_for_update()
    entry = db.scalar(query)
    if not entry:
        raise HTTPException(404, "Entrada no encontrada.")
    return entry


def version_check(entry, version):
    if entry.version != version:
        raise HTTPException(409, "La entrada ha cambiado. Recarga antes de guardar.")


def apply_input(db, entry, body):
    for name in ("occurred_on", "title", "content_markdown", "summary", "source",
                 "evidence_level", "activity_type", "technologies"):
        setattr(entry, name, getattr(body, name))
    entry.projects = [entity(db, Project, name) for name in body.projects]
    entry.tags = [entity(db, Tag, name) for name in body.tags]
    entry.references = [SourceReference(**r.model_dump()) for r in body.references]
    db.flush()
    related = set(body.related_entry_ids)
    if entry.id in related:
        raise HTTPException(422, "Una entrada no puede enlazarse consigo misma.")
    existing = set(db.scalars(select(Entry.id).where(Entry.id.in_(related)))) if related else set()
    if existing != related:
        raise HTTPException(422, "Hay referencias a entradas que no existen.")
    db.execute(delete(EntryRelation).where(EntryRelation.entry_id == entry.id))
    db.add_all([EntryRelation(entry_id=entry.id, related_id=other) for other in related])
    entry.updated_at = now()
    db.flush()


def create_entry(db, body):
    entry = Entry(status="draft")
    # Scalar required fields must be set before entity upserts trigger autoflush.
    for field in ("occurred_on", "title", "content_markdown", "summary", "source",
                  "evidence_level", "activity_type", "technologies"):
        setattr(entry, field, getattr(body, field))
    db.add(entry)
    db.flush()
    apply_input(db, entry, body)
    return entry


def month_range(month):
    try:
        year, number = map(int, month.split("-"))
        start = date(year, number, 1)
        return start, date(year, number, calendar.monthrange(year, number)[1])
    except (ValueError, TypeError):
        raise HTTPException(422, "Mes no válido.") from None


def filtered_query(q=None, occurred_on=None, date_from=None, date_to=None, month=None,
                   status=None, project_id=None, tag_id=None, sort="date"):
    query = select(Entry)
    if month:
        start, end = month_range(month)
        query = query.where(Entry.occurred_on.between(start, end))
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "El rango de fechas no es válido.")
    for value, clause in ((occurred_on, Entry.occurred_on == occurred_on),
                           (date_from, Entry.occurred_on >= date_from if date_from else None),
                           (date_to, Entry.occurred_on <= date_to if date_to else None),
                           (status, Entry.status == status)):
        if value:
            query = query.where(clause)
    if project_id:
        query = query.where(Entry.projects.any(Project.id == project_id))
    if tag_id:
        query = query.where(Entry.tags.any(Tag.id == tag_id))
    if q and q.strip():
        q = q.strip()
        es = func.websearch_to_tsquery("spanish", q)
        en = func.websearch_to_tsquery("english", q)
        query = query.where(or_(Entry.search_es.op("@@")(es), Entry.search_en.op("@@")(en),
                                Entry.search_text.op("%>")(q.lower())))
        if sort == "relevance":
            rank = func.ts_rank_cd(Entry.search_es, es) + func.ts_rank_cd(Entry.search_en, en)
            query = query.order_by((rank + func.word_similarity(q.lower(), Entry.search_text)).desc())
    return query.order_by(Entry.occurred_on.desc(), Entry.created_at.desc(), Entry.id.desc())


def markdown_export(entries):
    def section(entry):
        projects = ", ".join(x["name"] for x in entry["projects"])
        tags = ", ".join(x["name"] for x in entry["tags"])
        references = "\n".join(f"- {r['source_type']}: {r['source_reference']} ({r['evidence_level']})"
                               for r in entry["references"])
        return (f"# {entry['occurred_on']} — {entry['title']}\n\n"
                f"ID: {entry['id']}\nEstado: {entry['status']}\nActividad: {entry['activity_type']}\n"
                f"Evidencia: {entry['evidence_level']}\nProyectos: {projects}\nEtiquetas: {tags}\n"
                f"Fuente: {entry['source']}\n\n{entry['summary']}\n\n{entry['content_markdown']}\n\n"
                f"## Procedencia\n\n{references}\n")
    return "\n---\n\n".join(section(entry) for entry in entries)
