"""Module-neutral dashboard contract. Modules register validated widget providers."""
from typing import Any, Protocol
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database.session import get_db
from app.security.service import signed_in


class Widget(BaseModel):
    id: str
    version: int
    data: dict[str, Any]


class DashboardResponse(BaseModel):
    widgets: list[Widget]


class WidgetProvider(Protocol):
    id: str
    version: int

    def summary(self, db: Session) -> BaseModel: ...


providers: list[WidgetProvider] = []


def register(provider: WidgetProvider):
    if any(existing.id == provider.id for existing in providers):
        raise ValueError('Duplicate widget provider')
    providers.append(provider)


router = APIRouter(dependencies=[Depends(signed_in)], tags=['dashboard'])


@router.get('/dashboard', response_model=DashboardResponse)
def dashboard(db: Session = Depends(get_db, scope='function')):
    return DashboardResponse(widgets=[Widget(id=provider.id, version=provider.version,
        data=provider.summary(db).model_dump(mode='json')) for provider in providers])
