import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from sqlalchemy import select, func

from app.main import app
from app.database.models import Project, Tag, Entry
from tests.helpers import post


def payload(**changes):
    return {"occurred_on": "2026-09-03", "title": "Synthetic fixture: investigación",
            "content_markdown": "Investigando búsquedas. Testing deployment integrations.",
            "summary": "Fixture sintético, sin actividad real.", "projects": ["Proyecto sintético"],
            "tags": ["Fixture"], "evidence_level": "unverified", "activity_type": "research", **changes}


def test_normalized_unicode_names_validate_before_persistence(authenticated):
    assert post(authenticated, '/projects', {'name': 'ß' * 100}).status_code == 422
    assert post(authenticated, '/projects', {'name': '   '}).status_code == 422
    assert post(authenticated, '/journal/entries', payload(projects=['ß' * 120])).status_code == 422


def create(client, **changes):
    response = post(client, "/journal/entries", payload(**changes))
    assert response.status_code == 201, response.text
    return response.json()


def test_crud_publish_and_delete_confirmation(authenticated):
    entry = create(authenticated)
    assert uuid.UUID(entry["id"]).version == 7 and entry["status"] == "draft"
    assert authenticated.get(f"/api/v1/journal/entries/{entry['id']}").json() == entry
    edited = post(authenticated, f"/journal/entries/{entry['id']}",
                  payload(title="Synthetic edited", version=entry["version"]), method="PUT")
    assert edited.status_code == 200
    entry = edited.json()
    published = post(authenticated, f"/journal/entries/{entry['id']}/publish", {"version": entry["version"]})
    assert published.json()["published_at"] and published.json()["status"] == "published"
    assert post(authenticated, f"/journal/entries/{entry['id']}", {"version": 3, "confirm": False}, method="DELETE").status_code == 422
    assert post(authenticated, f"/journal/entries/{entry['id']}", {"version": 3, "confirm": True}, method="DELETE").status_code == 200
    assert authenticated.get(f"/api/v1/journal/entries/{entry['id']}").status_code == 404


def test_normalized_relations(authenticated, db):
    first = create(authenticated, projects=[" Proyecto sintético ", "proyecto sintético"], tags=["Fixture", "fixture"])
    second = create(authenticated, related_entry_ids=[first["id"]])
    assert len(first["projects"]) == len(first["tags"]) == 1
    assert second["related_entry_ids"] == [first["id"]]
    assert db.scalar(select(func.count()).select_from(Project)) == 1
    assert db.scalar(select(func.count()).select_from(Tag)) == 1
    assert post(authenticated, f"/journal/entries/{first['id']}",
        payload(version=1, related_entry_ids=[first["id"]]), method="PUT").status_code == 422


def test_reference_validation_and_transaction_rollback(authenticated):
    response = post(authenticated, "/journal/entries", payload(related_entry_ids=[str(uuid.uuid4())]))
    assert response.status_code == 422
    assert authenticated.get("/api/v1/journal/entries").json()["total"] == 0
    assert post(authenticated, "/journal/entries", payload(title="   ")).status_code == 422
    assert post(authenticated, "/journal/entries", payload(status="published")).status_code == 422


def test_filters_and_calendar_multiple_entries_per_day(authenticated):
    first = create(authenticated)
    create(authenticated, title="Other fixture")
    create(authenticated, occurred_on="2026-10-01", projects=["Other synthetic project"], tags=["Other"])
    assert authenticated.get("/api/v1/journal/entries?month=2026-09").json()["total"] == 2
    assert authenticated.get("/api/v1/journal/entries?occurred_on=2026-09-03").json()["total"] == 2
    assert authenticated.get("/api/v1/journal/entries", params={"project_id": first["projects"][0]["id"], "tag_id": first["tags"][0]["id"]}).json()["total"] == 2
    assert authenticated.get("/api/v1/journal/entries?date_from=2026-10-01&date_to=2026-09-01").status_code == 422
    assert authenticated.get("/api/v1/journal/calendar?month=2026-09").json()["days"] == [{"date": "2026-09-03", "count": 2}]
    assert authenticated.get("/api/v1/journal/entries?month=2026-99").status_code == 422
    page = authenticated.get("/api/v1/journal/entries?limit=1&offset=1").json()
    assert page["total"] == 3 and len(page["items"]) == 1


def test_fts_bilingual_and_trigram(authenticated):
    entry = create(authenticated)
    for term in ("investigar", "testing", "deploymnt", "búsqueda"):
        response = authenticated.get("/api/v1/journal/entries", params={"q": term, "sort": "relevance"})
        assert response.status_code == 200 and response.json()["total"] == 1, (term, response.text)
    post(authenticated, f"/journal/entries/{entry['id']}", payload(version=1,
        content_markdown="Nuevo contenido sintético", title="Otra entrada", summary=""), method="PUT")
    assert authenticated.get("/api/v1/journal/entries?q=testing").json()["total"] == 0
    assert authenticated.get("/api/v1/journal/entries", params={"q": "'; DROP TABLE users; --"}).status_code == 200


def test_export_entry_month_all(authenticated):
    entry = create(authenticated, references=[{"source_type": "fixture", "source_reference": "urn:synthetic:one", "evidence_level": "unverified"}])
    create(authenticated, occurred_on="2026-10-01")
    result = authenticated.get("/api/v1/journal/export", params={"entry_id": entry["id"]})
    assert len(result.json()["entries"]) == 1 and result.json()["schema_version"] == 1
    assert len(authenticated.get("/api/v1/journal/export?month=2026-09").json()["entries"]) == 1
    assert len(authenticated.get("/api/v1/journal/export").json()["entries"]) == 2
    markdown = authenticated.get("/api/v1/journal/export?format=markdown&month=2026-09")
    assert "Procedencia" in markdown.text and "urn:synthetic:one" in markdown.text
    assert "attachment" in markdown.headers["content-disposition"]


def test_conflicting_edits(authenticated):
    entry = create(authenticated)
    path = f"/journal/entries/{entry['id']}"
    assert post(authenticated, path, payload(version=1), method="PUT").status_code == 200
    assert post(authenticated, path, payload(version=1, title="Stale fixture"), method="PUT").status_code == 409


def test_concurrent_edits_only_one_wins(authenticated):
    entry = create(authenticated)
    cookies = dict(authenticated.cookies)
    headers = dict(authenticated.headers)
    def attempt(index):
        with TestClient(app, base_url="http://localhost:5173", cookies=cookies, headers=headers) as other:
            return post(other, f"/journal/entries/{entry['id']}", payload(version=1, title=f"Synthetic {index}"), method="PUT").status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, [1, 2])) == [200, 409]


def test_dashboard_uses_real_data_and_no_access_without_mfa(authenticated, db):
    response = authenticated.get("/api/v1/dashboard").json()
    data = response["widgets"][0]["data"]
    assert data["recent_entries"] == [] and data["month"]["entries"] == 0
    entry = create(authenticated, occurred_on=data["today"])
    post(authenticated, f"/journal/entries/{entry['id']}/publish", {"version": 1})
    data = authenticated.get("/api/v1/dashboard").json()["widgets"][0]["data"]
    assert data["today_entries"][0]["id"] == entry["id"] and data["month"]["entries"] == 1
    assert data["projects"][0]["count"] == 1
    db.expire_all()
    assert db.scalar(select(Entry)).status == "published"
    with TestClient(app) as other:
        assert other.get("/api/v1/dashboard").status_code == 401


def test_collector_bearer_never_accesses_journal(authenticated):
    token = post(authenticated, "/security/tokens", {"name": "fixture"}).json()["token"]
    with TestClient(app, headers={"Authorization": "Bearer " + token}) as collector:
        for endpoint in ("/journal/entries", "/dashboard", "/security/overview", "/auth/csrf"):
            assert collector.get("/api/v1" + endpoint).status_code == 401
