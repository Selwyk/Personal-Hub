from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json

from fastapi.testclient import TestClient
from sqlalchemy import select, func

from app.database.models import Candidate, Batch, Entry, CollectorToken
from app.main import app
from app.modules.ingestion.schemas import CandidateInput, content_hash, BatchInput
from app.modules.ingestion.service import ingest
from app.security.service import now
from app.database.session import session_factory
from tests.helpers import post


def candidate(**changes):
    return {"occurred_on": "2026-09-01", "title": "Synthetic candidate",
            "summary": "Fixture only", "detail_markdown": "Una propuesta, no una implementación.",
            "activity_type": "proposal", "projects": ["Proyecto sintético"], "tags": ["Fixture"],
            "technologies": [], "source_type": "fixture", "source_reference": "urn:synthetic:source",
            "source_date": "2026-09-01", "external_id": "synthetic-one", "evidence_level": "unverified",
            "collector_version": "fixture/1", "raw_metadata": {"synthetic": True}, **changes}


def collector_client(authenticated):
    token = post(authenticated, "/security/tokens", {"name": "fixture"}).json()["token"]
    return TestClient(app, headers={"Authorization": "Bearer " + token}), token


def send(client, key="synthetic-batch", items=None):
    return client.post("/api/v1/ingest/batches", json={"import_key": key, "candidates": items or [candidate()]})


def test_batch_idempotence_dedup_and_conflicts(authenticated, db):
    collector, _ = collector_client(authenticated)
    with collector:
        response = send(collector)
        assert response.status_code == 200 and response.json()["results"][0]["status"] == "created"
        repeat = send(collector)
        assert repeat.json()["replayed"] and repeat.json()["results"] == response.json()["results"]
        assert send(collector, items=[candidate(title="Changed")]).status_code == 409
        assert send(collector, "synthetic-second").json()["results"][0]["status"] == "duplicate"
        assert send(collector, "synthetic-conflict", [candidate(title="Changed")]).json()["results"][0]["status"] == "conflict"
        assert send(collector, "synthetic-hash-duplicate", [candidate(external_id="synthetic-two")]).json()["results"][0]["status"] == "duplicate"
    assert db.scalar(select(func.count()).select_from(Candidate)) == 1
    assert db.scalar(select(Candidate)).status == "pending_review"


def test_partial_errors_and_new_retry_key(authenticated, db):
    collector, _ = collector_client(authenticated)
    with collector:
        items = [candidate(), candidate(external_id="bad", occurred_on="not-a-date"),
                 candidate(external_id="hash", content_hash="0" * 64)]
        result = send(collector, items=items).json()
        assert [r["status"] for r in result["results"]] == ["created", "invalid", "invalid"]
        assert "not-a-date" not in json.dumps(result)
        assert send(collector, items=items).json()["replayed"]
        corrected = send(collector, "corrected", [candidate(), candidate(external_id="bad", title="Corrected fixture")])
        assert [r["status"] for r in corrected.json()["results"]] == ["duplicate", "created"]
    assert db.scalar(select(func.count()).select_from(Candidate)) == 2


def test_supplied_hash_and_preview_without_writes(authenticated, db):
    item = candidate()
    item["content_hash"] = content_hash(CandidateInput.model_validate(item))
    response = post(authenticated, "/ingest/preview", {"import_key": "preview", "candidates": [item]})
    assert response.json()["valid"] == 1
    assert db.scalar(select(func.count()).select_from(Batch)) == 0
    collector, _ = collector_client(authenticated)
    with collector:
        assert send(collector, items=[item]).json()["results"][0]["status"] == "created"


def test_accept_edit_discard_preserves_original_and_classification(authenticated, db):
    collector, _ = collector_client(authenticated)
    with collector:
        result = send(collector, items=[candidate(), candidate(title="Other fixture", external_id="two")]).json()
    first, second = [r["candidate_id"] for r in result["results"]]
    edited = candidate(title="Reviewed fixture")
    response = post(authenticated, f"/ingest/candidates/{first}", {"version": 1, "candidate": edited}, method="PUT")
    assert response.status_code == 200
    assert response.json()["original_candidate"]["title"] == "Synthetic candidate"
    assert post(authenticated, f"/ingest/candidates/{first}/accept", {"version": 1}).status_code == 409
    accepted = post(authenticated, f"/ingest/candidates/{first}/accept", {"version": 2})
    assert accepted.status_code == 200
    entry = authenticated.get("/api/v1/journal/entries/" + accepted.json()["entry_id"]).json()
    assert entry["status"] == "draft" and entry["activity_type"] == "proposal"
    assert entry["evidence_level"] == "unverified" and entry["title"] == "Reviewed fixture"
    assert entry["references"][0]["source_reference"] == "urn:synthetic:source"
    assert post(authenticated, f"/ingest/candidates/{first}/accept", {"version": 3}).status_code == 409
    assert post(authenticated, f"/ingest/candidates/{second}/discard", {"version": 1}).status_code == 200
    assert db.scalar(select(func.count()).select_from(Entry)) == 1
    assert authenticated.get("/api/v1/ingest/batches").json()["total"] == 1


def test_collector_cannot_approve_or_read_and_browser_session_cannot_ingest(authenticated):
    collector, _ = collector_client(authenticated)
    with collector:
        item = send(collector).json()["results"][0]["candidate_id"]
        assert collector.get("/api/v1/ingest/candidates").status_code == 401
        assert collector.post(f"/api/v1/ingest/candidates/{item}/accept", json={"version": 1}).status_code == 401
        assert collector.post("/api/v1/journal/entries", json={}).status_code == 401
    assert post(authenticated, "/ingest/batches", {"import_key": "browser", "candidates": [candidate()]}).status_code == 401


def test_revoked_and_expired_collector(authenticated, db):
    collector, _ = collector_client(authenticated)
    token = db.scalar(select(CollectorToken))
    token.expires_at = now() - timedelta(seconds=1)
    db.commit()
    with collector:
        assert send(collector).status_code == 401
        token.expires_at = None
        token.revoked_at = now()
        db.commit()
        assert send(collector).status_code == 401


def test_concurrent_import_and_acceptance(authenticated, db):
    body = BatchInput(import_key="concurrent-fixture", candidates=[candidate()])
    def insert_one(_):
        with session_factory()() as session:
            result = ingest(session, body)
            session.commit()
            return result["replayed"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(insert_one, range(2))) == [False, True]
    item = db.scalar(select(Candidate))
    cookies, headers = dict(authenticated.cookies), dict(authenticated.headers)
    def accept_one(_):
        with TestClient(app, base_url="http://localhost:5173", cookies=cookies, headers=headers) as client:
            return post(client, f"/ingest/candidates/{item.id}/accept", {"version": 1}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(accept_one, range(2))) == [200, 409]
    assert db.scalar(select(func.count()).select_from(Entry)) == 1


def test_source_identity_cannot_be_replaced(authenticated):
    collector, _ = collector_client(authenticated)
    with collector:
        item = send(collector).json()["results"][0]["candidate_id"]
    assert post(authenticated, f"/ingest/candidates/{item}", {"version": 1,
        "candidate": candidate(source_reference="urn:synthetic:changed")}, method="PUT").status_code == 422


def test_collector_provenance_becomes_multiple_draft_references(authenticated):
    refs = [{"source_type": "personal-import", "source_reference": "urn:synthetic:import:12",
             "source_date": "2026-09-01", "external_id": "12:comment:1",
             "evidence_level": "confirmed"},
            {"source_type": "manual-reference", "source_reference": "urn:synthetic:manual:one",
             "source_date": "2026-09-01", "external_id": "event-one",
             "evidence_level": "unverified"}]
    collector, _ = collector_client(authenticated)
    with collector:
        response = send(collector, items=[candidate(raw_metadata={"source_references": refs})])
        assert response.status_code == 200
        item = response.json()["results"][0]["candidate_id"]
    accepted = post(authenticated, f"/ingest/candidates/{item}/accept", {"version": 1})
    assert accepted.status_code == 200 and accepted.json()["status"] == "draft"
    entry = authenticated.get("/api/v1/journal/entries/" + accepted.json()["entry_id"]).json()
    assert {ref["source_type"] for ref in entry["references"]} == {"personal-import", "manual-reference"}
    assert entry["status"] == "draft"


def test_generic_personal_candidate_reaches_pending_review(authenticated, db):
    generated = CandidateInput.model_validate(candidate(title="Synthetic personal activity", activity_type="research"))
    collector, _ = collector_client(authenticated)
    with collector:
        response = send(collector, key="generated-activity", items=[generated.model_dump(mode="json")])
        assert response.status_code == 200
        assert response.json()["results"][0]["status"] == "created"
    stored = db.scalar(select(Candidate))
    assert stored.status == "pending_review"
    assert db.scalar(select(func.count()).select_from(Entry)) == 0


def test_cli_preview_apply_idempotent(tmp_path, monkeypatch, capsys, db):
    from app.cli import main
    path = tmp_path / "synthetic.json"
    path.write_text(json.dumps({"import_key": "cli-fixture", "candidates": [candidate()]}))
    monkeypatch.setattr("sys.argv", ["app.cli", "import-batch", "--input", str(path)])
    main()
    assert db.scalar(select(func.count()).select_from(Candidate)) == 0
    monkeypatch.setattr("sys.argv", ["app.cli", "import-batch", "--input", str(path), "--apply"])
    main()
    main()
    assert db.scalar(select(func.count()).select_from(Candidate)) == 1
    assert '"replayed": true' in capsys.readouterr().out


def test_cli_invalid_import_never_writes(tmp_path, monkeypatch, db):
    import pytest
    from app.cli import main
    path = tmp_path / "synthetic.json"
    path.write_text(json.dumps({"import_key": "invalid-cli", "candidates": [candidate(occurred_on="bad")]}))
    monkeypatch.setattr("sys.argv", ["app.cli", "import-batch", "--input", str(path), "--apply"])
    with pytest.raises(SystemExit):
        main()
    assert db.scalar(select(func.count()).select_from(Candidate)) == 0
