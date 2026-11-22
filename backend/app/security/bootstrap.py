"""Optional first-start provisioning. Never replace an existing owner."""
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.config import settings
from app.database.models import User
from app.database.session import session_factory
from app.security.service import event, password_hasher


def bootstrap_user():
    config = settings()
    if not config.bootstrap_password:
        return False
    with session_factory()() as db, db.begin():
        if db.scalar(select(User.id).limit(1)):
            return False
        # Singleton uniqueness arbitrates competing workers or interactive CLI setup.
        user_id = db.scalar(insert(User).values(
            username=config.bootstrap_username,
            password_hash=password_hasher.hash(config.bootstrap_password.get_secret_value()),
            must_change_password=True,
        ).on_conflict_do_nothing().returning(User.id))
        if user_id:
            event(db, "initial_user_provisioned", user_id)
        return user_id is not None
