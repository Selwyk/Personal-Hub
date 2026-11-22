from fastapi import HTTPException
from sqlalchemy import select

from app.core.catalogs import entity
from app.database.models import Task, Project, Tag
from app.security.service import now


def task_dict(task):
    fields = ("id", "title", "description", "status", "priority", "due_on", "parent_id",
              "completed_at", "created_at", "updated_at", "version")
    return {**{name: getattr(task, name) for name in fields},
            "projects": [{"id": p.id, "name": p.name} for p in sorted(task.projects, key=lambda p: p.normalized_name)],
            "tags": [{"id": t.id, "name": t.name} for t in sorted(task.tags, key=lambda t: t.normalized_name)]}


def find_task(db, task_id, lock=False):
    query = select(Task).where(Task.id == task_id)
    if lock:
        query = query.with_for_update()
    task = db.scalar(query)
    if task is None:
        raise HTTPException(404, "Tarea no encontrada.")
    return task


def version_check(task, version):
    if task.version != version:
        raise HTTPException(409, "La tarea ha cambiado. Recarga antes de guardar.")


def set_status(task, status):
    task.completed_at = (task.completed_at or now()) if status == "completed" else None
    task.status = status


def apply_input(db, task, body):
    for name in ("title", "description", "priority", "due_on"):
        setattr(task, name, getattr(body, name))
    set_status(task, body.status)
    task.updated_at = now()
    # Required scalars are assigned before catalog upserts trigger autoflush.
    task.projects = [entity(db, Project, name) for name in body.projects]
    task.tags = [entity(db, Tag, name) for name in body.tags]
    db.flush()


def filtered_query(status=None, project_id=None, tag_id=None):
    query = select(Task)
    if status:
        query = query.where(Task.status == status)
    if project_id:
        query = query.where(Task.projects.any(Project.id == project_id))
    if tag_id:
        query = query.where(Task.tags.any(Tag.id == tag_id))
    return query.order_by(Task.due_on.asc().nulls_last(), Task.created_at.desc(), Task.id.desc())
