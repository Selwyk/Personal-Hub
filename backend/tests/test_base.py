import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.database.models import User
from app.database.session import engine


def test_migration_and_uuidv7(db):
    user = User(username="synthetic", password_hash="synthetic-only")
    db.add(user)
    db.flush()
    assert isinstance(user.id, uuid.UUID) and user.id.version == 7
    tables = inspect(engine()).get_table_names()
    assert "journal_entries" in tables and "webauthn_credentials" in tables
    assert db.scalar(text("SELECT extname FROM pg_extension WHERE extname='pg_trgm'"))


def test_single_user_constraint(db):
    db.add_all([User(username="fixture-one", password_hash="fixture"),
                User(username="fixture-two", password_hash="fixture")])
    with pytest.raises(IntegrityError):
        db.flush()


def test_config_timeouts_and_production_cookie():
    config = Settings()
    assert config.session_idle_timeout_seconds == 86400
    assert config.session_absolute_timeout_seconds == 604800
    assert config.trusted_device_days == 30
    with pytest.raises(ValidationError):
        Settings(app_public_url="https://example.invalid", webauthn_rp_id="example.invalid",
                 webauthn_origins="https://example.invalid", cookie_secure=False)
    with pytest.raises(ValidationError):
        Settings(session_idle_timeout_seconds=0)
