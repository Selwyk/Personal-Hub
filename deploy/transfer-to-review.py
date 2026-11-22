"""Export all journal content, or import it as pending review only.

Mount this administrative tool read-only into API 1.1.0. Export stdout is PRIVATE;
redirect it to an ignored file. Import defaults to a read-only preview.
"""
import argparse
import json
import pathlib
import sys
from uuid import UUID

if pathlib.Path('/app/app').is_dir():
    sys.path.insert(0, '/app')

from fastapi import HTTPException  # noqa: E402
from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402
from app.database.models import Candidate, Entry, User  # noqa: E402
from app.database.session import session_factory  # noqa: E402
from app.modules.ingestion.schemas import BatchInput, CandidateInput, canonical_hash, content_hash  # noqa: E402
from app.modules.ingestion.service import ingest  # noqa: E402
from app.modules.journal.service import entry_dict  # noqa: E402

MAX_BYTES = 50 * 1024 * 1024


def from_entry(raw):
    return CandidateInput.model_validate({
        'occurred_on': raw['occurred_on'], 'title': raw['title'], 'summary': raw['summary'],
        'detail_markdown': raw['content_markdown'], 'activity_type': raw['activity_type'],
        'evidence_level': raw['evidence_level'], 'technologies': raw['technologies'],
        'projects': [p['name'] for p in raw['projects']], 'tags': [t['name'] for t in raw['tags']],
        'source_type': 'personalhub_transfer', 'source_reference': 'personalhub://entry',
        'external_id': raw['id'], 'collector_version': 'review-transfer-1',
        'raw_metadata': {'transfer': {'kind': 'entry', 'original_entry': raw}},
    }).model_dump(mode='json')


def export_review(db):
    entries = db.scalars(select(Entry).order_by(Entry.id)).all()
    entry_ids = {row.id for row in entries}
    rows = [from_entry(entry_dict(db, row)) for row in entries]
    for row in db.scalars(select(Candidate).order_by(Candidate.id)):
        if row.entry_id in entry_ids:
            continue  # The current entry already represents this accepted activity.
        latest = CandidateInput.model_validate({**row.review_payload, 'content_hash': None})
        payload = latest.model_dump(mode='json')
        payload.update(source_type='personalhub_transfer', source_reference='personalhub://candidate',
                       external_id=str(row.id), collector_version='review-transfer-1', content_hash=None)
        payload['raw_metadata'] = {'transfer': {'kind': 'candidate', 'original_id': str(row.id),
            'original_payload': row.original_payload, 'review_payload': row.review_payload,
            'original_version': row.version, 'original_status': row.status,
            'original_batch_id': str(row.batch_id)}}
        rows.append(payload)
    return {'schema_version': 1, 'purpose': 'pending_review_transfer', 'candidates': rows}


def validate(raw):
    if (not isinstance(raw, dict) or set(raw) != {'schema_version', 'purpose', 'candidates'}
            or type(raw['schema_version']) is not int or raw['schema_version'] != 1
            or raw['purpose'] != 'pending_review_transfer' or not isinstance(raw['candidates'], list)
            or len(raw['candidates']) > 10000):
        raise ValueError('invalid_transfer')
    rows = [CandidateInput.model_validate(row) for row in raw['candidates']]
    identities = set()
    for row in rows:
        if (row.source_type != 'personalhub_transfer'
                or row.source_reference not in ('personalhub://entry', 'personalhub://candidate')
                or not row.external_id or UUID(row.external_id).version != 7):
            raise ValueError('invalid_identity')
        identity = (row.source_reference, row.external_id)
        if identity in identities:
            raise ValueError('duplicate_identity')
        identities.add(identity)
        if row.content_hash and row.content_hash != content_hash(row):
            raise ValueError('invalid_hash')
    return rows


def transfer(db, rows, apply=False):
    query = select(User)
    if apply:
        query = query.with_for_update()
    if not db.scalar(query):
        raise ValueError('target_owner_required')
    new = []
    for row in rows:
        if row.source_reference == 'personalhub://entry':
            existing_entry = db.get(Entry, UUID(row.external_id))
            if existing_entry:
                existing = CandidateInput.model_validate(from_entry(entry_dict(db, existing_entry)))
                if content_hash(existing) != content_hash(row):
                    raise ValueError('existing_entry_conflict')
                continue  # Never demote or delete an existing target entry.
        existing = db.scalar(select(Candidate).where(Candidate.source_type == row.source_type,
            Candidate.source_reference == row.source_reference, Candidate.external_id == row.external_id))
        if existing:
            if canonical_hash(existing.original_payload) != canonical_hash(row.model_dump(mode='json')):
                raise ValueError('existing_candidate_conflict')
            continue  # Retry must not undo subsequent acceptance, editing or discard.
        new.append(row)
    if apply:
        for row in new:
            payload = row.model_dump(mode='json')
            key = 'review-transfer-v1:' + canonical_hash(payload)
            result = ingest(db, BatchInput(import_key=key, candidates=[payload]))
            if any(item['status'] not in ('created', 'duplicate') for item in result['results']):
                raise ValueError('ingestion_conflict')
    return {'mode': 'apply' if apply else 'preview', 'total': len(rows),
            'to_create': len(new), 'already_present': len(rows) - len(new)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--export', action='store_true')
    group.add_argument('--input', help='Private file or - for stdin')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        if args.export and args.apply:
            raise ValueError('export_read_only')
        rows = None
        if args.input:
            stream = sys.stdin.buffer if args.input == '-' else pathlib.Path(args.input).open('rb')
            try:
                contents = stream.read(MAX_BYTES + 1)
            finally:
                if args.input != '-':
                    stream.close()
            if len(contents) > MAX_BYTES:
                raise ValueError('file_too_large')
            rows = validate(json.loads(contents.decode('utf-8-sig')))
        with session_factory()() as db, db.begin():
            if not args.apply:
                db.execute(text('SET TRANSACTION READ ONLY'))
            result = export_review(db) if args.export else transfer(db, rows, args.apply)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError, HTTPException):
        print(json.dumps({'error': 'invalid_or_conflicting_transfer', 'written': 0}), file=sys.stderr)
        return 2
    except SQLAlchemyError:
        print(json.dumps({'error': 'database_error_rolled_back', 'written': 0}), file=sys.stderr)
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
