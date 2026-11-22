"""Integration tests always use a separate, disposable *_test database."""
import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

original_url = os.environ["DATABASE_URL"]
os.environ["DATABASE_URL"] = original_url.rsplit("/", 1)[0] + "/hub_test"

from app.core.config import settings  # noqa: E402
from app.database.models import Base  # noqa: E402
from app.database.session import engine, session_factory  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def migrated():
    assert engine().url.database.endswith("_test"), "Refuse to reset non-test database"
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture(autouse=True)
def clean_database(migrated):
    tables = ",".join('"' + t.name + '"' for t in Base.metadata.sorted_tables)
    with engine().begin() as db:
        db.execute(text(f"TRUNCATE {tables} CASCADE"))
    settings.cache_clear()


@pytest.fixture
def db():
    with session_factory()() as session:
        yield session
        session.rollback()


@pytest.fixture
def user(db):
    from app.database.models import User
    from app.security.service import password_hasher
    from tests.helpers import PASSWORD
    user = User(username="synthetic", password_hash=password_hasher.hash(PASSWORD))
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app, base_url="http://localhost:5173") as client:
        client.headers["Origin"] = "http://localhost:5173"
        response = client.get("/api/v1/auth/csrf")
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        yield client


@pytest.fixture
def enrolled_user(user, db):
    from app.database.models import TotpFactor
    from app.security.service import encrypt
    from tests.helpers import SECRET
    user.mfa_initialized = True
    db.add(TotpFactor(user_id=user.id, secret_encrypted=encrypt(SECRET), last_step=-1))
    db.commit()
    return user


@pytest.fixture
def authenticated(client, enrolled_user):
    import pyotp
    from tests.helpers import PASSWORD, SECRET, post
    assert post(client, "/auth/login", {"username": "synthetic", "password": PASSWORD}).status_code == 200
    client.used_totp = pyotp.TOTP(SECRET).now()
    assert post(client, "/auth/totp/verify", {"code": client.used_totp}).status_code == 200
    return client
