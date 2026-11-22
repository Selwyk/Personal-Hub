"""Authenticated catalogs shared by modules within this application's own database."""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.core.catalogs import entity, EntityOut
from app.database.models import Project, Tag
from app.database.session import get_db
from app.security.schemas import Named
from app.security.service import signed_in

router = APIRouter(dependencies=[Depends(signed_in)], tags=["catalogs"])

@router.get("/projects", response_model=list[EntityOut])
def projects(db: DbSession = Depends(get_db, scope="function")):
    return [{"id": row.id, "name": row.name} for row in db.scalars(select(Project).order_by(Project.name))]


@router.post("/projects", response_model=EntityOut)
def create_project(body: Named, db: DbSession = Depends(get_db, scope="function")):
    row = entity(db, Project, body.name)
    return {"id": row.id, "name": row.name}


@router.get("/tags", response_model=list[EntityOut])
def tags(db: DbSession = Depends(get_db, scope="function")):
    return [{"id": row.id, "name": row.name} for row in db.scalars(select(Tag).order_by(Tag.name))]


@router.post("/tags", response_model=EntityOut)
def create_tag(body: Named, db: DbSession = Depends(get_db, scope="function")):
    row = entity(db, Tag, body.name)
    return {"id": row.id, "name": row.name}
