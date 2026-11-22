"""Tasks contract against disposable PostgreSQL; every record is synthetic."""
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func, text
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.database.models import Task, Project, Tag
from app.main import app
from tests.helpers import post
from tests.test_journal import create as create_entry


@pytest.fixture
def tasks(monkeypatch):
    monkeypatch.setenv("TASKS_ENABLED", "true")
    settings.cache_clear()
    yield
    settings.cache_clear()


def payload(**changes):
    return {"title": "Tarea sintética", "description": "Descripción de prueba sin datos reales",
            "projects": ["Proyecto sintético"], "tags": ["Fixture"], **changes}


def create(client, **changes):
    response = post(client, "/tasks", payload(**changes))
    assert response.status_code == 201, response.text
    return response.json()


def test_default_disabled_and_capabilities(authenticated, monkeypatch):
    monkeypatch.delenv("TASKS_ENABLED", raising=False)
    settings.cache_clear()
    assert authenticated.get("/api/v1/modules").json() == {"tasks": False}
    for method, path, body in (("GET", "/tasks", None), ("GET", "/tasks/export", None),
                               ("POST", "/tasks", payload()),
                               ("PUT", f"/tasks/{uuid.uuid4()}", payload(version=1)),
                               ("POST", f"/tasks/{uuid.uuid4()}/complete", {"version": 1})):
        assert post(authenticated, path, body, method=method).status_code == 404


def test_create_read_edit_complete_and_reopen(tasks, authenticated):
    assert authenticated.get("/api/v1/modules").json() == {"tasks": True}
    task = create(authenticated)
    assert uuid.UUID(task["id"]).version == 7
    assert task["status"] == "todo" and task["priority"] == "normal"
    assert task["completed_at"] is task["parent_id"] is task["due_on"] is None
    path = "/tasks/" + task["id"]
    assert authenticated.get("/api/v1" + path).json() == task
    edited = post(authenticated, path, payload(version=1, title="Título editado", status="in_progress",
                  priority="urgent", due_on="2026-11-20"), method="PUT").json()
    assert edited["version"] == 2 and edited["due_on"] == "2026-11-20"
    completed = post(authenticated, path + "/complete", {"version": 2}).json()
    assert completed["status"] == "completed" and completed["completed_at"]
    assert completed["version"] == 3
    assert post(authenticated, path + "/complete", {"version": 3}).json() == completed
    assert post(authenticated, path + "/complete", {"version": 2}).status_code == 409
    reopened = post(authenticated, path, payload(version=3, projects=[], tags=[], due_on=None), method="PUT").json()
    assert reopened["status"] == "todo" and reopened["completed_at"] is None
    assert reopened["projects"] == reopened["tags"] == [] and reopened["due_on"] is None
    assert reopened["version"] == 4


def test_completed_create_and_edit_preserve_completion_time(tasks, authenticated):
    task = create(authenticated, status="completed")
    assert task["completed_at"]
    result = post(authenticated, "/tasks/" + task["id"], payload(version=1, status="completed"), method="PUT").json()
    assert result["completed_at"] == task["completed_at"]


def test_reuses_catalogs_without_changing_journal(tasks, authenticated, db):
    entry = create_entry(authenticated)
    task = create(authenticated, projects=[" Proyecto sintético ", "proyecto sintético"], tags=["Fixture", "fixture"])
    assert task["projects"] == entry["projects"] and task["tags"] == entry["tags"]
    assert db.scalar(select(func.count()).select_from(Project)) == 1
    assert db.scalar(select(func.count()).select_from(Tag)) == 1
    assert authenticated.get("/api/v1/journal/entries/" + entry["id"]).json() == entry


@pytest.mark.parametrize("changes", [
    {"title": "   "}, {"title": "x" * 241}, {"description": "x" * 65537},
    {"status": "archived"}, {"priority": "critical"}, {"due_on": "2026-02-30"},
    {"due_on": "2026-11-21T12:30:00Z"}, {"tags": ["ß" * 120]},
    {"tags": [" "]}, {"projects": ["p"] * 31}, {"parent_id": str(uuid.uuid4())},
    {"recurrence": "daily"}, {"completed_at": "2026-01-01T00:00:00Z"},
])
def test_invalid_input_has_no_side_effects(tasks, authenticated, db, changes):
    result = post(authenticated, "/tasks", payload(**changes))
    assert result.status_code == 422, result.text
    assert db.scalar(select(func.count()).select_from(Task)) == 0
    assert db.scalar(select(func.count()).select_from(Project)) == 0


def test_filters_order_pagination_and_export(tasks, authenticated):
    one = create(authenticated, due_on="2026-11-23")
    two = create(authenticated, due_on="2026-11-22", status="completed", projects=["Otro proyecto"], tags=["Otra"])
    three = create(authenticated, title="Sin fecha")
    page = authenticated.get("/api/v1/tasks?limit=2").json()
    assert [t["id"] for t in page["items"]] == [two["id"], one["id"]]
    assert page["total"] == 3
    assert authenticated.get("/api/v1/tasks?limit=2&offset=2").json()["items"] == [three]
    assert authenticated.get("/api/v1/tasks?status=completed").json()["items"] == [two]
    for key, value in (("project_id", one["projects"][0]["id"]), ("tag_id", one["tags"][0]["id"])):
        assert authenticated.get("/api/v1/tasks", params={key: value}).json()["total"] == 2
    for query in ("limit=0", "limit=201", "offset=-1", "status=invalid", "project_id=bad"):
        assert authenticated.get("/api/v1/tasks?" + query).status_code == 422
    exported = authenticated.get("/api/v1/tasks/export")
    assert exported.status_code == 200 and "attachment" in exported.headers["content-disposition"]
    assert exported.json() == {"schema_version": 1, "tasks": [two, one, three]}


def test_not_found_and_conflicting_update(tasks, authenticated):
    missing = str(uuid.uuid4())
    assert authenticated.get("/api/v1/tasks/" + missing).status_code == 404
    assert post(authenticated, "/tasks/" + missing, payload(version=1), method="PUT").status_code == 404
    assert post(authenticated, "/tasks/" + missing + "/complete", {"version": 1}).status_code == 404
    task = create(authenticated)
    assert post(authenticated, "/tasks/" + task["id"], payload(version=1), method="PUT").status_code == 200
    assert post(authenticated, "/tasks/" + task["id"], payload(version=1, projects=["Stale"]), method="PUT").status_code == 409
    assert post(authenticated, "/tasks/" + task["id"], payload(version=0), method="PUT").status_code == 422
    assert post(authenticated, "/tasks/" + task["id"], payload(), method="PUT").status_code == 422
    assert authenticated.get("/api/v1/projects").json() == task["projects"]


def test_concurrent_edit_and_complete_only_one_wins(tasks, authenticated):
    task = create(authenticated)
    cookies = dict(authenticated.cookies)
    headers = dict(authenticated.headers)
    def attempt(index):
        with TestClient(app, base_url="http://localhost:5173", cookies=cookies, headers=headers) as client:
            path = "/tasks/" + task["id"]
            return (post(client, path, payload(version=1, title="Concurrent fixture"), method="PUT")
                    if index == 0 else post(client, path + "/complete", {"version": 1})).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, [0, 1])) == [200, 409]


def test_auth_csrf_origin_and_collector_boundaries(tasks, client, authenticated):
    with TestClient(app) as anonymous:
        for path in ("/tasks", "/tasks/export", "/modules"):
            assert anonymous.get("/api/v1" + path).status_code == 401
    token = post(authenticated, "/security/tokens", {"name": "Synthetic tasks denial"}).json()["token"]
    with TestClient(app, headers={"Authorization": "Bearer " + token}) as collector:
        assert collector.get("/api/v1/tasks").status_code == 401
        assert collector.get("/api/v1/tasks/export").status_code == 401
        assert collector.post("/api/v1/tasks", json=payload()).status_code == 401
        assert collector.get("/api/v1/modules").status_code == 401
    headers = dict(authenticated.headers)
    authenticated.headers["X-CSRF-Token"] = "invalid"
    assert post(authenticated, "/tasks", payload()).status_code == 403
    authenticated.headers.update(headers)
    authenticated.headers["Origin"] = "https://wrong.example.invalid"
    assert post(authenticated, "/tasks", payload()).status_code == 403


def test_enrollment_session_cannot_access_tasks(tasks, client, user):
    from tests.helpers import PASSWORD
    response = post(client, "/auth/login", {"username": "synthetic", "password": PASSWORD})
    assert response.json()["level"] == "enrollment"
    assert client.get("/api/v1/tasks").status_code == 403
    assert post(client, "/tasks", payload()).status_code == 403
    assert client.get("/api/v1/tasks/export").status_code == 403


@pytest.mark.parametrize("clause", ["status='bad'", "priority='bad'", "version=0",
                                    "status='completed'", "parent_id=id", "title=' '"])
def test_database_constraints(tasks, authenticated, db, clause):
    task = create(authenticated)
    with pytest.raises(IntegrityError):
        db.execute(text(f"UPDATE tasks SET {clause} WHERE id=:id"), {"id": task["id"]})
        db.flush()
    db.rollback()
