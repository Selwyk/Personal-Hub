"""Synthetic credentials for an isolated backup/restore test ONLY."""
from datetime import timedelta
from app.database.session import engine, session_factory
from app.database.models import User, Session, Device, Challenge, CollectorToken
from app.security.service import now, digest, password_hasher
from app.modules.journal.service import create_entry
from app.modules.journal.schemas import EntryInput
assert engine().url.database == 'hub_restore_test', 'Refuse non-disposable target'
with session_factory()() as db, db.begin():
    user = User(username='restore-fixture', password_hash=password_hasher.hash('Synthetic restore password only!'))
    db.add(user)
    db.flush()
    session = Session(user_id=user.id, token_hash=digest('synthetic-session'), csrf_token='synthetic-csrf',
                      level='full', security_version=1, last_seen_at=now(), expires_at=now() + timedelta(days=7))
    db.add(session)
    db.flush()
    db.add_all([Device(user_id=user.id, token_hash=digest('synthetic-device'), name='fixture',
                       security_version=1, expires_at=now() + timedelta(days=30)),
                Challenge(session_id=session.id, purpose='fixture', value='synthetic', expires_at=now() + timedelta(minutes=5)),
                CollectorToken(user_id=user.id, token_hash=digest('synthetic-collector'), name='fixture')])
    create_entry(db, EntryInput(occurred_on='2026-09-01', title='SYNTHETIC restore fixture', content_markdown='Synthetic data only.'))
print('Synthetic restore fixture ready')
