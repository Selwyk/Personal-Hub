"""Only synthetic data; external administrative utility on real PostgreSQL."""
import importlib.util
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from sqlalchemy import select, text
import pytest
from app.database.models import (Candidate, Entry, User, Credential, Session, Device,
                                 CollectorToken, TotpFactor, RecoveryCode, Challenge)
from app.database.session import session_factory
from app.modules.ingestion.schemas import BatchInput
from app.modules.ingestion.service import ingest
from tests.helpers import post, SoftwareAuthenticator
from tests.test_journal_transfer import export_data as synthetic_export, importer as entry_tool

export_data = synthetic_export
importer = entry_tool


@pytest.fixture
def review_tool():
    source = Path('/deploy/transfer-to-review.py')
    if not source.exists():
        pytest.skip('Mount deploy at /deploy')
    spec = importlib.util.spec_from_file_location('review_transfer', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(tool, rows, apply=False):
    with session_factory()() as session, session.begin():
        if not apply:
            session.execute(text('SET TRANSACTION READ ONLY'))
        return tool.transfer(session, tool.validate(rows), apply)


def test_export_all_statuses_and_latest_correction(review_tool, export_data, user, db):
    base = review_tool.from_entry(export_data['entries'][0])
    for index, status in enumerate(('pending_review', 'accepted', 'discarded')):
        raw = {**base, 'external_id': f'fixture-{index}', 'title': f'Fixture {index}'}
        result = ingest(db, BatchInput(import_key=f'fixture-{index}', candidates=[raw]))
        row = db.get(Candidate, result['results'][0]['candidate_id'])
        row.status = status
        if index == 0:
            row.review_payload = {**row.review_payload, 'title': 'Latest synthetic correction'}
    db.commit()
    result = review_tool.export_review(db)
    assert len(result['candidates']) == 3
    assert 'Latest synthetic correction' in [row['title'] for row in result['candidates']]
    assert review_tool.validate(result)[0].activity_type == 'research'


def test_all_pending_and_retry_preserves_subsequent_review(review_tool, export_data, authenticated, db):
    raw = {'schema_version': 1, 'purpose': 'pending_review_transfer',
           'candidates': [review_tool.from_entry(row) for row in export_data['entries']]}
    authenticator = SoftwareAuthenticator()
    options = post(authenticated, '/auth/webauthn/register/options', {}).json()
    assert post(authenticated, '/auth/webauthn/register/verify', {
        'name': 'Synthetic key', 'challenge_id': options['challenge_id'],
        'credential': authenticator.register(options['options'])}).status_code == 200
    models = (User, Credential, Session, Device, CollectorToken, TotpFactor, RecoveryCode, Challenge)
    def snapshot():
        return {m.__tablename__: [dict(row) for row in db.execute(select(m.__table__)).mappings()]
                for m in models}
    db.rollback()
    before = snapshot()
    assert run(review_tool, raw)['to_create'] == 2
    assert db.scalars(select(Candidate)).all() == []
    assert run(review_tool, raw, True)['to_create'] == 2
    db.expire_all()
    rows = db.scalars(select(Candidate)).all()
    assert len(rows) == 2 and all(row.status == 'pending_review' for row in rows)
    assert db.scalars(select(Entry)).all() == []
    rows[0].status = 'discarded'
    rows[1].review_payload = {**rows[1].review_payload, 'title': 'Target correction'}
    db.commit()
    assert run(review_tool, raw, True)['already_present'] == 2
    db.expire_all()
    assert snapshot() == before
    assert db.get(Candidate, rows[0].id).status == 'discarded'
    assert db.get(Candidate, rows[1].id).review_payload['title'] == 'Target correction'


def test_accepted_activity_exports_once(review_tool, importer, export_data, user, db):
    importer.transfer(db, importer.validate_export(export_data), True)
    entry = db.scalar(select(Entry))
    raw = review_tool.from_entry(export_data['entries'][0])
    result = ingest(db, BatchInput(import_key='fixture-accepted', candidates=[raw]))
    candidate = db.get(Candidate, result['results'][0]['candidate_id'])
    candidate.status = 'accepted'
    candidate.entry_id = entry.id
    db.commit()
    assert len(review_tool.export_review(db)['candidates']) == 2


def test_existing_entries_skip_and_conflict_rolls_back(review_tool, importer, export_data, user, db):
    importer.transfer(db, importer.validate_export(export_data), True)
    db.commit()
    raw = review_tool.export_review(db)
    assert run(review_tool, raw, True)['already_present'] == 2
    raw['candidates'][1]['title'] = 'Changed source'
    with pytest.raises(ValueError):
        run(review_tool, raw, True)
    assert db.scalars(select(Candidate)).all() == []


def test_invalid_and_auth_payload_rejected(review_tool, export_data):
    raw = {'schema_version': 1, 'purpose': 'pending_review_transfer',
           'candidates': [review_tool.from_entry(export_data['entries'][0])]}
    raw['user'] = {'password': 'synthetic'}
    with pytest.raises(ValueError):
        review_tool.validate(raw)
    raw.pop('user')
    raw['candidates'].append(raw['candidates'][0])
    with pytest.raises(ValueError):
        review_tool.validate(raw)


def test_concurrent_imports_are_idempotent(review_tool, export_data, user):
    raw = {'schema_version': 1, 'purpose': 'pending_review_transfer',
           'candidates': [review_tool.from_entry(row) for row in export_data['entries']]}
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: run(review_tool, raw, True), range(2)))
    assert sorted(result['to_create'] for result in results) == [0, 2]
