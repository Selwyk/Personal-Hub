from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.database.models import Task
from app.database.session import get_db
from app.modules.tasks.schemas import (TaskInput, TaskUpdate, TaskVersion, TaskOut,
                                       TaskPage, TaskExport, TaskStatus)
from app.modules.tasks.service import (apply_input, filtered_query, find_task,
                                       set_status, task_dict, version_check)
from app.security.service import signed_in, now


def enabled():
    if not settings().tasks_enabled:
        raise HTTPException(404, "Módulo no habilitado.")


router = APIRouter(prefix="/tasks", tags=["tasks"],
                   dependencies=[Depends(signed_in), Depends(enabled)])


@router.post("", response_model=TaskOut, status_code=201)
def create(body: TaskInput, db: Session = Depends(get_db, scope="function")):
    task = Task(title=body.title, description=body.description, priority=body.priority,
                due_on=body.due_on)
    set_status(task, body.status)
    db.add(task)
    db.flush()
    apply_input(db, task, body)
    return task_dict(task)


@router.get("", response_model=TaskPage)
def listing(status: TaskStatus | None = None, project_id: UUID | None = None,
            tag_id: UUID | None = None, offset: int = Query(default=0, ge=0),
            limit: int = Query(default=50, ge=1, le=200),
            db: Session = Depends(get_db, scope="function")):
    query = filtered_query(status, project_id, tag_id)
    return {"items": [task_dict(t) for t in db.scalars(query.offset(offset).limit(limit))],
            "total": db.scalar(select(func.count()).select_from(query.order_by(None).subquery())),
            "offset": offset, "limit": limit}


# Static path must precede /{task_id}.
@router.get("/export")
def export(db: Session = Depends(get_db, scope="function")):
    payload = TaskExport(tasks=[task_dict(t) for t in db.scalars(filtered_query())])
    return Response(payload.model_dump_json(indent=2), media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="tasks.json"'})


@router.get("/{task_id}", response_model=TaskOut)
def detail(task_id: UUID, db: Session = Depends(get_db, scope="function")):
    return task_dict(find_task(db, task_id))


@router.put("/{task_id}", response_model=TaskOut)
def edit(task_id: UUID, body: TaskUpdate, db: Session = Depends(get_db, scope="function")):
    task = find_task(db, task_id, lock=True)
    version_check(task, body.version)
    apply_input(db, task, body)
    task.version += 1
    db.flush()
    return task_dict(task)


@router.post("/{task_id}/complete", response_model=TaskOut)
def complete(task_id: UUID, body: TaskVersion, db: Session = Depends(get_db, scope="function")):
    task = find_task(db, task_id, lock=True)
    version_check(task, body.version)
    if task.status != "completed":
        set_status(task, "completed")
        task.updated_at = now()
        task.version += 1
    db.flush()
    return task_dict(task)
