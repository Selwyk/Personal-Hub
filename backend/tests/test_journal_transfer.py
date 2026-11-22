"""Synthetic PostgreSQL transfer tests; mount deploy at /deploy in the test runner."""
import copy
import importlib.util
import io
import json
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, text

from app.database.models import (Entry, User, Credential, Session, Device, CollectorToken,
                                 TotpFactor, RecoveryCode, Challenge)
from app.database.session import session_factory
from app.modules.journal.schemas import EntryOut
from app.modules.journal.service import entry_dict
from tests.helpers import post, SoftwareAuthenticator
from app.security.service import now, digest, new_session, add_challenge
from fastapi import Response


@pytest.fixture
def importer():
    source = Path('/deploy/import-journal.py')
    if not source.exists():
        pytest.skip('Mount deploy sources read-only at /deploy')
    spec = importlib.util.spec_from_file_location('journal_transfer', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def export_data():
    # UUIDs and all text in this fixture are deliberately synthetic.
    first = '01900000-0000-7000-8000-000000000001'
    second = '01900000-0000-7000-8000-000000000002'
    entry = {'id': first, 'occurred_on': '2026-09-01', 'title': 'Investigación sintética',
             'content_markdown': '# Synthetic research\nSolo investigación.', 'summary': 'Fixture',
             'status': 'published', 'source': 'manual', 'evidence_level': 'inferred',
             'activity_type': 'research', 'technologies': ['Python'], 'version': 4,
             'created_at': '2026-09-01T10:00:00Z', 'updated_at': '2026-09-02T11:00:00Z',
             'published_at': '2026-09-02T11:00:00Z',
             'projects': [{'id': '01900000-0000-7000-8000-000000000003', 'name': 'Proyecto sintético'}],
             'tags': [{'id': '01900000-0000-7000-8000-000000000004', 'name': 'fixture'}],
             'related_entry_ids': [second],
             'references': [{'source_type': 'synthetic', 'source_reference': 'fixture://research',
                             'source_date': '2026-09-01', 'external_id': 'fixture-1',
                             'evidence_level': 'unverified'}]}
    other = {**copy.deepcopy(entry), 'id': second, 'title': 'Propuesta sintética',
             'status': 'draft', 'published_at': None, 'activity_type': 'proposal',
             'related_entry_ids': [first]}
    return {'schema_version': 1, 'entries': [entry, other]}


def run(importer, data, apply=False):
    rows = importer.validate_export(data)
    with session_factory()() as db, db.begin():
        if not apply:
            db.execute(text('SET TRANSACTION READ ONLY'))
        return importer.transfer(db, rows, apply=apply)


def test_preview_is_read_only_and_apply_preserves_all_entry_data(importer, export_data, user, db):
    assert run(importer, export_data)['to_create'] == 2
    assert db.scalar(select(func.count()).select_from(Entry)) == 0
    result = run(importer, export_data, apply=True)
    assert result == {'mode': 'apply', 'total': 2, 'to_create': 2, 'already_present': 0,
                      'draft': 1, 'published': 1}
    for raw in export_data['entries']:
        actual = EntryOut.model_validate(entry_dict(db, db.get(Entry, UUID(raw['id']))))
        assert importer.signature(actual) == importer.signature(EntryOut.model_validate(raw))
        assert actual.id == UUID(raw['id'])
        assert actual.projects[0].id != UUID(raw['projects'][0]['id'])
    # Circular and forward links survive; entities are normalized shared records.
    assert run(importer, export_data, apply=True)['already_present'] == 2
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Entry)) == 2


def test_transfer_keeps_password_mfa_sessions_devices_and_tokens(importer, export_data, authenticated, db):
    credential = SoftwareAuthenticator()
    options = post(authenticated, '/auth/webauthn/register/options', {}).json()
    assert post(authenticated, '/auth/webauthn/register/verify', {
        'name': 'Synthetic FIDO', 'challenge_id': options['challenge_id'],
        'credential': credential.register(options['options'])}).status_code == 200
    owner = db.scalar(select(User))
    active_session = new_session(db, Response(), owner, 'full')
    db.add(Device(user_id=owner.id, token_hash=digest('synthetic device'),
                  name='Synthetic device', expires_at=now() + timedelta(days=30),
                  security_version=owner.security_version))
    db.add(CollectorToken(user_id=owner.id, token_hash=digest('synthetic token'), name='Synthetic token'))
    db.add(RecoveryCode(user_id=owner.id, code_hash=digest('synthetic recovery')))
    add_challenge(db, active_session, 'key_login', 'synthetic challenge')
    db.commit()
    models = (User, Credential, Session, Device, CollectorToken, TotpFactor, RecoveryCode, Challenge)
    def snapshot():
        return {m.__tablename__: [dict(row) for row in db.execute(select(m.__table__)).mappings()]
                for m in models}
    before = snapshot()
    run(importer, export_data, apply=True)
    db.expire_all()
    assert snapshot() == before
    assert db.get(User, owner.id).mfa_initialized
    assert db.scalar(select(Credential)).revoked_at is None


def test_reimport_never_overwrites_conflicting_entry(importer, export_data, user, db):
    run(importer, export_data, apply=True)
    changed = copy.deepcopy(export_data)
    changed['entries'][0]['title'] = 'Different synthetic content'
    # Also include a new entry: nothing is inserted when any identity conflicts.
    extra = copy.deepcopy(changed['entries'][1])
    extra['id'] = '01900000-0000-7000-8000-000000000009'
    changed['entries'].insert(0, extra)
    with pytest.raises(importer.TransferError, match='conflict_no_overwrite'):
        run(importer, changed, apply=True)
    assert db.scalar(select(func.count()).select_from(Entry)) == 2


def test_concurrent_retries_are_idempotent(importer, export_data, user, db):
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: run(importer, export_data, apply=True), range(2)))
    assert sorted(r['to_create'] for r in results) == [0, 2]
    assert db.scalar(select(func.count()).select_from(Entry)) == 2


@pytest.mark.parametrize('change,code', [
    ('duplicate', 'duplicate_export_ids'), ('self', 'self_relation'),
    ('publication', 'publication_inconsistent'), ('version', 'invalid_entry_version'),
    ('timezone', 'timestamp_requires_timezone'), ('schema', 'unsupported_export_version'),
    ('uuid', 'entry_id_requires_uuidv7'),
])
def test_invalid_export_is_rejected(importer, export_data, change, code):
    row = export_data['entries'][0]
    if change == 'duplicate':
        export_data['entries'].append(copy.deepcopy(row))
    if change == 'self':
        row['related_entry_ids'] = [row['id']]
    if change == 'publication':
        row['published_at'] = None
    if change == 'version':
        row['version'] = 0
    if change == 'timezone':
        row['created_at'] = '2026-09-01T10:00:00'
    if change == 'schema':
        export_data['schema_version'] = 2
    if change == 'uuid':
        row['id'] = '00000000-0000-4000-8000-000000000001'
    with pytest.raises(importer.TransferError, match=code):
        importer.validate_export(export_data)


def test_missing_links_do_not_write_anything(importer, export_data, user, db):
    export_data['entries'] = export_data['entries'][:1]
    with pytest.raises(importer.TransferError, match='related_entries_missing'):
        run(importer, export_data, apply=True)
    assert db.scalar(select(func.count()).select_from(Entry)) == 0


def test_owner_is_required_and_auth_payload_is_rejected(importer, export_data):
    with pytest.raises(importer.TransferError, match='target_owner_required'):
        run(importer, export_data, apply=True)
    export_data['password_hash'] = 'synthetic forbidden field'
    with pytest.raises(ValidationError):
        importer.validate_export(export_data)


def test_cli_preview_apply_replay_and_private_output(importer, export_data, user, monkeypatch, tmp_path, capsys):
    source = tmp_path / 'journal.json'
    source.write_text(json.dumps(export_data), encoding='utf-8')
    args = ['import-journal.py', '--input', str(source)]
    monkeypatch.setattr('sys.argv', args)
    assert importer.main() == 0
    assert json.loads(capsys.readouterr().out)['mode'] == 'preview'
    monkeypatch.setattr('sys.argv', args + ['--apply'])
    assert importer.main() == 0
    assert json.loads(capsys.readouterr().out)['to_create'] == 2
    assert importer.main() == 0
    output = capsys.readouterr().out
    assert json.loads(output)['already_present'] == 2
    assert 'Investigación' not in output and export_data['entries'][0]['id'] not in output


def test_failure_after_scalar_insert_rolls_back_entire_import(importer, export_data, user,
                                                           monkeypatch, tmp_path, capsys, db):
    source = tmp_path / 'journal.json'
    source.write_text(json.dumps(export_data), encoding='utf-8')
    monkeypatch.setattr('sys.argv', ['import-journal.py', '--input', str(source), '--apply'])
    def fail(*args):
        raise ValueError('synthetic private details that must not be printed')
    monkeypatch.setattr(importer, 'apply_input', fail)
    assert importer.main() == 2
    assert 'private details' not in capsys.readouterr().err
    assert db.scalar(select(func.count()).select_from(Entry)) == 0


def test_input_size_and_utf8_bom(importer, export_data, tmp_path, monkeypatch):
    source = tmp_path / 'journal.json'
    source.write_text(json.dumps(export_data), encoding='utf-8-sig')
    assert len(importer.load_export(source)) == 2
    monkeypatch.setattr(importer, 'MAX_BYTES', 10)
    with pytest.raises(importer.TransferError, match='export_exceeds_50_mib'):
        importer.load_export(source)


def test_private_export_can_be_read_from_stdin(importer, export_data, monkeypatch):
    stream = io.TextIOWrapper(io.BytesIO(json.dumps(export_data).encode()), encoding='utf-8')
    monkeypatch.setattr('sys.stdin', stream)
    assert len(importer.load_export('-')) == 2


def test_actual_api_export_round_trip_keeps_publication(importer, authenticated, db):
    original = post(authenticated, '/journal/entries', {
        'occurred_on': '2026-09-03', 'title': 'Fixture actual API export',
        'content_markdown': 'Synthetic only', 'projects': ['Fixture project'],
        'tags': ['fixture'], 'evidence_level': 'inferred', 'activity_type': 'proposal'}).json()
    original = post(authenticated, '/journal/entries/' + original['id'] + '/publish',
                    {'version': original['version']}).json()
    exported = authenticated.get('/api/v1/journal/export?format=json').json()
    assert post(authenticated, '/journal/entries/' + original['id'],
                {'version': original['version'], 'confirm': True}, method='DELETE').status_code == 200
    assert run(importer, exported, apply=True)['to_create'] == 1
    actual = authenticated.get('/api/v1/journal/entries/' + original['id']).json()
    assert importer.signature(EntryOut.model_validate(actual)) == importer.signature(
        EntryOut.model_validate(original))
