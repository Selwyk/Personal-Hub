"""Only disposable, explicitly named hub_e2e data. Never a production seed."""
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from app.database.session import engine, session_factory
from app.database.models import Base, User, TotpFactor
from app.security.service import encrypt, password_hasher
from tests.helpers import PASSWORD, SECRET

assert engine().url.database == 'hub_e2e', 'Refuse to seed another database'
command.upgrade(Config('alembic.ini'), 'head')
with engine().begin() as connection:
    connection.execute(text('TRUNCATE ' + ','.join('"' + t.name + '"' for t in Base.metadata.sorted_tables) + ' CASCADE'))
with session_factory()() as db, db.begin():
    user = User(username='synthetic', password_hash=password_hasher.hash(PASSWORD), mfa_initialized=True)
    db.add(user)
    db.flush()
    db.add(TotpFactor(user_id=user.id, secret_encrypted=encrypt(SECRET), last_step=-1))
print('Synthetic E2E user ready')
