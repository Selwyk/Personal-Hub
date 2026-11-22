"""Create one synthetic owner in a NEW identity-guarded fixture instance. No reset."""
import os

from sqlalchemy import select
from app.core.config import settings
from app.database.session import session_factory
from app.database.models import User, TotpFactor
from app.security.service import encrypt, password_hasher


def main():
    if os.environ.get('HUB_SYNTHETIC_INSTANCE') != '1' or not settings().isolation_required:
        raise RuntimeError('Synthetic isolated instance required')
    password, secret = os.environ['SYNTHETIC_PASSWORD'], os.environ['SYNTHETIC_TOTP_SECRET']
    if len(password) < 20 or len(secret) < 32:
        raise RuntimeError('Invalid synthetic fixture credentials')
    with session_factory()() as db, db.begin():
        if db.scalar(select(User.id)):
            raise RuntimeError('Refuse to seed an existing owner')
        user = User(username='synthetic', password_hash=password_hasher.hash(password), mfa_initialized=True)
        db.add(user)
        db.flush()
        db.add(TotpFactor(user_id=user.id, secret_encrypted=encrypt(secret), last_step=-1))
    print('New synthetic instance owner prepared')


if __name__ == '__main__':
    main()
