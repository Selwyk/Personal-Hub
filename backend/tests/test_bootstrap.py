"""Per-install synthetic credentials; no real owner or data is used."""
from concurrent.futures import ThreadPoolExecutor

import pyotp
import pytest
from pydantic import ValidationError
from sqlalchemy import select, func

from app.core.config import settings, Settings
from app.database.models import User, Session
from app.security.bootstrap import bootstrap_user
from app.security.service import verify_password
from tests.helpers import PASSWORD, post

TEMPORARY = "Synthetic temporary credential 2026!"


@pytest.fixture
def bootstrap(monkeypatch):
    monkeypatch.setenv("BOOTSTRAP_PASSWORD", TEMPORARY)
    settings.cache_clear()
    yield
    settings.cache_clear()


def test_disabled_without_secret(monkeypatch, db):
    monkeypatch.delenv("BOOTSTRAP_PASSWORD", raising=False)
    settings.cache_clear()
    assert not bootstrap_user()
    assert db.scalar(select(func.count()).select_from(User)) == 0


def test_startup_creates_owner_and_requires_password_before_mfa(bootstrap, client, db):
    user = db.scalar(select(User))
    assert user.username == "admin" and user.must_change_password
    assert user.password_hash.startswith("$argon2id$")
    assert TEMPORARY not in repr(settings())
    assert verify_password(user.password_hash, TEMPORARY)
    result = post(client, "/auth/login", {"username": "admin", "password": TEMPORARY})
    assert result.json()["level"] == "initial_password"
    old_cookie = client.cookies.get("hub_session")
    for route in ("/auth/totp/setup", "/auth/webauthn/register/options", "/security/recovery-codes"):
        assert post(client, route, {}).status_code == 403
    assert client.get("/api/v1/journal/entries").status_code == 403
    assert post(client, "/auth/initial-password", {"new_password": "short"}).status_code == 422
    assert post(client, "/auth/initial-password", {"new_password": TEMPORARY}).status_code == 422
    result = post(client, "/auth/initial-password", {"new_password": PASSWORD})
    assert result.json()["level"] == "enrollment"
    assert old_cookie != client.cookies.get("hub_session")
    assert client.get("/api/v1/dashboard").status_code == 403
    assert post(client, "/auth/initial-password", {"new_password": PASSWORD}).status_code == 403
    setup = post(client, "/auth/totp/setup", {}).json()
    result = post(client, "/auth/totp/confirm", {
        "challenge_id": setup["challenge_id"], "code": pyotp.TOTP(setup["secret"]).now()})
    assert result.status_code == 200 and result.json()["level"] == "full"
    assert len(result.json()["recovery_codes"]) == 10
    assert client.get("/api/v1/journal/entries").status_code == 200
    db.expire_all()
    assert not db.get(User, user.id).must_change_password
    assert not bootstrap_user()
    assert post(client, "/auth/login", {"username": "admin", "password": TEMPORARY}).status_code == 401
    client.cookies.set("hub_session", old_cookie)
    assert client.get("/api/v1/auth/me").status_code == 401


def test_existing_owner_not_changed(bootstrap, user, db):
    original = (user.id, user.username, user.password_hash, user.security_version)
    assert not bootstrap_user()
    db.refresh(user)
    assert (user.id, user.username, user.password_hash, user.security_version) == original
    assert not user.must_change_password


def test_concurrent_first_starts_create_exactly_one_owner(bootstrap, db):
    with ThreadPoolExecutor(max_workers=4) as executor:
        assert sum(executor.map(lambda _: bootstrap_user(), range(4))) == 1
    assert db.scalar(select(func.count()).select_from(User)) == 1


def test_initial_password_requires_session_and_csrf(bootstrap, client, db):
    assert post(client, "/auth/initial-password", {"new_password": PASSWORD}).status_code == 403
    post(client, "/auth/login", {"username": "admin", "password": TEMPORARY})
    token = client.headers.pop("X-CSRF-Token")
    assert post(client, "/auth/initial-password", {"new_password": PASSWORD}).status_code == 403
    client.headers["X-CSRF-Token"] = token
    session = db.scalar(select(Session).where(Session.level == "initial_password"))
    from datetime import timedelta
    from app.security.service import now
    session.expires_at = now() - timedelta(seconds=1)
    db.commit()
    assert post(client, "/auth/initial-password", {"new_password": PASSWORD}).status_code == 401


def test_invalid_bootstrap_config_does_not_echo_secret():
    with pytest.raises(ValidationError) as error:
        Settings(bootstrap_password="short", _env_file=None)
    # The model validator reports only the policy, never a password value.
    assert "BOOTSTRAP_PASSWORD must contain" in str(error.value)
    assert "short" not in str(error.value)
