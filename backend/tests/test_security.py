from datetime import timedelta

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database.models import (User, Session, TotpFactor, Credential, Device, Challenge,
                                 CollectorToken, RecoveryCode)
from app.main import app
from app.security.service import (now, encrypt, digest, password_hasher, decrypt,
                                  invalidate_after_restore, generate_recovery)
from tests.helpers import PASSWORD, SECRET, post, SoftwareAuthenticator, corrupt_challenge
from concurrent.futures import ThreadPoolExecutor
from app.database.session import session_factory
from app.security.service import verify_totp


@pytest.fixture
def user(db):
    user = User(username="synthetic", password_hash=password_hasher.hash(PASSWORD))
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def client():
    with TestClient(app, base_url="http://localhost:5173") as client:
        client.headers["Origin"] = "http://localhost:5173"
        response = client.get("/api/v1/auth/csrf")
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        yield client


@pytest.fixture
def enrolled_user(user, db):
    user.mfa_initialized = True
    db.add(TotpFactor(user_id=user.id, secret_encrypted=encrypt(SECRET), last_step=-1))
    db.commit()
    return user


@pytest.fixture
def authenticated(client, enrolled_user):
    assert post(client, "/auth/login", {"username": "synthetic", "password": PASSWORD}).status_code == 200
    client.used_totp = pyotp.TOTP(SECRET).now()
    assert post(client, "/auth/totp/verify", {"code": client.used_totp}).status_code == 200
    return client


def test_password_hash_rotation_and_csrf(client, user, db):
    old_cookie = client.cookies.get("hub_session")
    assert user.password_hash.startswith("$argon2id$")
    response = post(client, "/auth/login", {"username": "synthetic", "password": PASSWORD})
    assert response.json()["level"] == "enrollment"
    assert client.cookies.get("hub_session") != old_cookie
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    client.headers.pop("X-CSRF-Token")
    assert post(client, "/auth/logout", {}).status_code == 403
    assert all(row.token_hash != client.cookies.get("hub_session") for row in db.scalars(select(Session)))


@pytest.mark.parametrize("username,password", [("wrong", PASSWORD), ("synthetic", "wrong")])
def test_generic_password_errors(client, user, username, password):
    response = post(client, "/auth/login", {"username": username, "password": password})
    assert response.status_code == 401
    assert "wrong" not in response.text and "synthetic" not in response.text


def test_origin_and_validation_never_echo_secrets(client, user):
    client.headers["Origin"] = "https://untrusted.invalid"
    assert post(client, "/auth/login", {"username": "synthetic", "password": PASSWORD}).status_code == 403
    client.headers["Origin"] = "http://localhost:5173"
    response = post(client, "/auth/login", {"username": "synthetic", "password": "x" * 1025})
    assert response.status_code == 422 and "x" * 20 not in response.text


def test_rate_limit(client, user):
    for _ in range(10):
        assert post(client, "/auth/login", {"username": "synthetic", "password": "wrong"}).status_code == 401
    assert post(client, "/auth/login", {"username": "synthetic", "password": "wrong"}).status_code == 429


def test_totp_enrollment_encrypted_and_confirmed(client, user, db):
    post(client, "/auth/login", {"username": "synthetic", "password": PASSWORD})
    setup = post(client, "/auth/totp/setup", {}).json()
    assert "<svg" in setup["qr_svg"]
    assert not db.scalar(select(TotpFactor))
    result = post(client, "/auth/totp/confirm", {"challenge_id": setup["challenge_id"],
                 "code": pyotp.TOTP(setup["secret"]).now()})
    assert result.status_code == 200, result.text
    assert len(result.json()["recovery_codes"]) == 10
    factor = db.scalar(select(TotpFactor))
    assert factor.secret_encrypted != setup["secret"]
    assert decrypt(factor.secret_encrypted) == setup["secret"]
    assert client.get("/api/v1/auth/me").json()["level"] == "full"


def test_failed_enrollment_consumes_challenge(client, user, db):
    post(client, "/auth/login", {"username": "synthetic", "password": PASSWORD})
    setup = post(client, "/auth/totp/setup", {}).json()
    payload = {"challenge_id": setup["challenge_id"], "code": "invalid"}
    assert post(client, "/auth/totp/confirm", payload).status_code == 401
    payload["code"] = pyotp.TOTP(setup["secret"]).now()
    assert post(client, "/auth/totp/confirm", payload).status_code == 400
    assert not db.scalar(select(TotpFactor))


@pytest.mark.parametrize("code", ["invalid", "000", "expired"])
def test_totp_negative(client, enrolled_user, code):
    post(client, "/auth/login", {"username": "synthetic", "password": PASSWORD})
    if code == "expired":
        code = pyotp.TOTP(SECRET).at(now().timestamp() - 120)
    assert post(client, "/auth/totp/verify", {"code": code}).status_code == 401


def test_totp_cannot_reuse(authenticated):
    post(authenticated, "/auth/logout", {})
    post(authenticated, "/auth/login", {"username": "synthetic", "password": PASSWORD})
    assert post(authenticated, "/auth/totp/verify", {"code": authenticated.used_totp}).status_code == 401


@pytest.mark.parametrize("kind", ["idle", "absolute", "revoked"])
def test_session_expiration(authenticated, db, kind):
    session = db.scalar(select(Session).where(Session.level == "full"))
    if kind == "idle":
        session.last_seen_at = now() - timedelta(hours=24, seconds=1)
    elif kind == "absolute":
        session.expires_at = now() - timedelta(seconds=1)
    else:
        session.revoked_at = now()
    db.commit()
    assert authenticated.get("/api/v1/auth/me").status_code == 401


def test_totp_concurrent_single_consumption(enrolled_user):
    code = pyotp.TOTP(SECRET).now()
    def attempt():
        with session_factory()() as session:
            result = verify_totp(session, enrolled_user.id, code)
            session.commit()
            return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: attempt(), range(2))) == [False, True]


def test_expired_step_up_requires_both_factors(authenticated, db):
    session = db.scalar(select(Session).where(Session.level == "full"))
    session.step_up_at = now() - timedelta(minutes=5, seconds=1)
    factor = db.scalar(select(TotpFactor))
    factor.last_step = -1
    db.commit()
    assert post(authenticated, "/security/tokens", {"name": "synthetic"}).status_code == 403
    assert post(authenticated, "/auth/step-up", {"password": "wrong", "code": pyotp.TOTP(SECRET).now()}).status_code == 401
    assert post(authenticated, "/auth/step-up", {"password": PASSWORD, "code": pyotp.TOTP(SECRET).now()}).status_code == 200
    assert post(authenticated, "/security/tokens", {"name": "synthetic"}).status_code == 200


def test_device_revocation_revokes_derived_session(authenticated, db, enrolled_user):
    session = db.scalar(select(Session).where(Session.level == "full"))
    device = Device(user_id=enrolled_user.id, token_hash=digest("fixture-device"), name="fixture",
                    expires_at=now() + timedelta(days=30), security_version=1)
    db.add(device)
    db.flush()
    session.device_id = device.id
    db.commit()
    assert post(authenticated, f"/security/devices/{device.id}", method="DELETE").status_code == 200
    assert authenticated.get("/api/v1/auth/me").status_code == 401


def test_factor_change_invalidates_sessions(authenticated, db):
    _, _, payload = register_key(authenticated)
    assert post(authenticated, "/auth/webauthn/register/verify", payload).json()["login_required"]
    assert authenticated.get("/api/v1/auth/me").status_code == 401


def test_malformed_webauthn_is_generic(client, user):
    post(client, "/auth/login", {"username": "synthetic", "password": PASSWORD})
    options = post(client, "/auth/webauthn/register/options", {}).json()
    response = post(client, "/auth/webauthn/register/verify", {
        "challenge_id": options["challenge_id"], "name": "fixture", "credential": {}})
    assert response.status_code == 401


def test_cli_initial_user_is_hashed(monkeypatch, db):
    from app.cli import main
    monkeypatch.setattr("sys.argv", ["app.cli", "create-user"])
    monkeypatch.setattr("builtins.input", lambda _: "fixture-cli")
    monkeypatch.setattr("getpass.getpass", lambda _: PASSWORD)
    main()
    user = db.scalar(select(User))
    assert password_hasher.verify(user.password_hash, PASSWORD)
    with pytest.raises(SystemExit):
        main()


def test_cli_restore_command(authenticated, monkeypatch, capsys, db):
    from app.cli import main
    monkeypatch.setattr("sys.argv", ["app.cli", "security", "invalidate-after-restore"])
    main()
    output = capsys.readouterr().out
    assert '"sessions"' in output and PASSWORD not in output
    assert authenticated.get("/api/v1/auth/me").status_code == 401


def test_absolute_expiry_not_extended(authenticated, db):
    session = db.scalar(select(Session).where(Session.level == "full"))
    expiry = session.expires_at
    assert authenticated.get("/api/v1/auth/me").status_code == 200
    db.refresh(session)
    assert session.expires_at == expiry


def test_trusted_device_fixed_expiry_and_sensitive_mfa(client, enrolled_user, db):
    post(client, "/auth/login", {"username": "synthetic", "password": PASSWORD})
    result = post(client, "/auth/totp/verify", {"code": pyotp.TOTP(SECRET).now(), "trust_device": True})
    assert result.status_code == 200
    device = db.scalar(select(Device))
    expires = device.expires_at
    assert timedelta(days=29, hours=23) < expires - now() <= timedelta(days=30)
    post(client, "/auth/logout", {})
    login = post(client, "/auth/login", {"username": "synthetic", "password": PASSWORD})
    assert login.json()["level"] == "full"
    assert post(client, "/security/tokens", {"name": "synthetic"}).status_code == 403
    db.refresh(device)
    assert device.expires_at == expires
    device.expires_at = now() - timedelta(seconds=1)
    db.commit()
    assert client.get("/api/v1/auth/me").status_code == 401
    client.get("/api/v1/auth/csrf")


def test_password_change_revokes_every_session_and_device(authenticated, db):
    response = post(authenticated, "/security/password", {"new_password": "Another synthetic password 2026!"})
    assert response.status_code == 200
    assert all(s.revoked_at for s in db.scalars(select(Session).where(Session.user_id.is_not(None))))
    assert authenticated.get("/api/v1/auth/me").status_code == 401


def test_last_factor_cannot_be_deleted(authenticated):
    assert post(authenticated, "/security/totp", method="DELETE").status_code == 409


def test_recovery_single_use_restricted_and_no_bootstrap_bypass(client, enrolled_user, db):
    codes = generate_recovery(db, enrolled_user.id)
    db.commit()
    post(client, "/auth/login", {"username": "synthetic", "password": PASSWORD})
    response = post(client, "/auth/recover", {"code": codes[0]})
    assert response.json()["level"] == "recovery"
    assert client.get("/api/v1/security/overview").status_code == 403
    post(client, "/auth/logout", {})
    assert post(client, "/auth/login", {"username": "synthetic", "password": PASSWORD}).json()["level"] == "password"
    assert post(client, "/auth/totp/setup", {}).status_code == 403
    assert post(client, "/auth/recover", {"code": codes[0]}).status_code == 401
    assert post(client, "/auth/recover", {"code": codes[1]}).status_code == 200


def test_regenerating_recovery_invalidates_old(authenticated, db):
    first = post(authenticated, "/security/recovery-codes", {}).json()["recovery_codes"]
    second = post(authenticated, "/security/recovery-codes", {}).json()["recovery_codes"]
    assert set(first).isdisjoint(second)
    assert db.scalar(select(RecoveryCode).where(RecoveryCode.code_hash == digest(first[0]))).used_at


def test_token_hash_and_revocation(authenticated, db):
    response = post(authenticated, "/security/tokens", {"name": "synthetic"})
    assert response.status_code == 200
    token = db.scalar(select(CollectorToken))
    assert token.token_hash == digest(response.json()["token"])
    assert response.json()["token"] not in authenticated.get("/api/v1/security/overview").text
    assert post(authenticated, f"/security/tokens/{token.id}", method="DELETE").status_code == 200


def register_key(client):
    authenticator = SoftwareAuthenticator()
    options = post(client, "/auth/webauthn/register/options", {}).json()
    payload = {"challenge_id": options["challenge_id"], "name": "Software fixture",
               "credential": authenticator.register(options["options"])}
    return authenticator, options, payload


def test_webauthn_registration_and_login_real_crypto(client, user, db):
    post(client, "/auth/login", {"username": "synthetic", "password": PASSWORD})
    authenticator, _, payload = register_key(client)
    assert post(client, "/auth/webauthn/register/verify", payload).status_code == 200
    key = db.scalar(select(Credential))
    assert key.public_key and key.name == "Software fixture"
    post(client, "/auth/logout", {})
    post(client, "/auth/login", {"username": "synthetic", "password": PASSWORD})
    options = post(client, "/auth/webauthn/authenticate/options", {}).json()
    response = post(client, "/auth/webauthn/authenticate/verify", {
        "challenge_id": options["challenge_id"],
        "credential": authenticator.authenticate(options["options"])})
    assert response.status_code == 200, response.text
    db.refresh(key)
    assert key.sign_count == 1


@pytest.mark.parametrize("kind", ["origin", "challenge", "uv", "rp", "expired"])
def test_webauthn_registration_negative(client, user, db, kind):
    post(client, "/auth/login", {"username": "synthetic", "password": PASSWORD})
    authenticator, options, payload = register_key(client)
    if kind == "origin":
        payload["credential"] = authenticator.register(options["options"], origin="https://evil.invalid")
    elif kind == "challenge":
        corrupt_challenge(payload["credential"])
    elif kind == "uv":
        payload["credential"] = authenticator.register(options["options"], flags=0x41)
    elif kind == "rp":
        options["options"]["rp"]["id"] = "wrong.invalid"
        payload["credential"] = authenticator.register(options["options"])
    else:
        challenge = db.get(Challenge, payload["challenge_id"])
        challenge.expires_at = now() - timedelta(seconds=1)
        db.commit()
    assert post(client, "/auth/webauthn/register/verify", payload).status_code in (400, 401)
    assert not db.scalar(select(Credential))


@pytest.mark.parametrize("kind", ["origin", "signature", "uv", "rp", "challenge", "revoked"])
def test_webauthn_authentication_negative(client, user, db, kind):
    post(client, "/auth/login", {"username": "synthetic", "password": PASSWORD})
    authenticator, _, payload = register_key(client)
    assert post(client, "/auth/webauthn/register/verify", payload).status_code == 200
    post(client, "/auth/logout", {})
    post(client, "/auth/login", {"username": "synthetic", "password": PASSWORD})
    options = post(client, "/auth/webauthn/authenticate/options", {}).json()
    credential = authenticator.authenticate(options["options"],
        origin="https://evil.invalid" if kind == "origin" else "http://localhost:5173",
        flags=1 if kind == "uv" else 5, rp="evil" if kind == "rp" else "localhost",
        wrong_signature=kind == "signature")
    if kind == "challenge":
        corrupt_challenge(credential)
    if kind == "revoked":
        key = db.scalar(select(Credential))
        key.revoked_at = now()
        db.commit()
    assert post(client, "/auth/webauthn/authenticate/verify", {
        "challenge_id": options["challenge_id"], "credential": credential}).status_code == 401
    assert post(client, "/auth/webauthn/authenticate/verify", {
        "challenge_id": options["challenge_id"], "credential": credential}).status_code == 400


def test_restore_invalidation_idempotent(authenticated, db, enrolled_user):
    post(authenticated, "/security/tokens", {"name": "synthetic"})
    session = db.scalar(select(Session).where(Session.level == "full"))
    db.add(Device(user_id=enrolled_user.id, token_hash=digest("fixture-device"), name="synthetic",
                  expires_at=now() + timedelta(days=30), security_version=1))
    db.add(Challenge(session_id=session.id, purpose="fixture", value="synthetic",
                     expires_at=now() + timedelta(minutes=5)))
    db.commit()
    counts = invalidate_after_restore(db)
    db.commit()
    assert all(counts[key] >= 1 for key in counts)
    assert all(value == 0 for value in invalidate_after_restore(db).values())
    db.commit()
    assert authenticated.get("/api/v1/auth/me").status_code == 401


def test_webauthn_step_up_rotates_the_specific_session(client, user, db):
    post(client, "/auth/login", {"username": "synthetic", "password": PASSWORD})
    authenticator, _, payload = register_key(client)
    assert post(client, "/auth/webauthn/register/verify", payload).status_code == 200
    session = db.scalar(select(Session).where(Session.level == "full", Session.revoked_at.is_(None)))
    session.created_at = now() - timedelta(hours=1)
    session.step_up_at = None
    db.commit()
    assert post(client, "/security/tokens", {"name": "fixture"}).status_code == 403
    options = post(client, "/auth/webauthn/authenticate/options", {}).json()
    assert post(client, "/auth/webauthn/authenticate/verify", {
        "challenge_id": options["challenge_id"], "password": PASSWORD,
        "credential": authenticator.authenticate(options["options"])}).status_code == 200
    assert post(client, "/security/tokens", {"name": "fixture"}).status_code == 200
    db.refresh(session)
    assert session.revoked_at is not None


def test_revocation_between_throttle_and_step_up_cannot_issue_session(authenticated, db, monkeypatch):
    from app.security import routes
    from sqlalchemy import update
    original = routes.throttle
    def revoke_after_commit(database, request, operation, user_id=None, **kwargs):
        original(database, request, operation, user_id, **kwargs)
        with session_factory()() as other, other.begin():
            other.execute(update(Session).where(Session.user_id == user_id).values(revoked_at=now()))
    monkeypatch.setattr(routes, 'throttle', revoke_after_commit)
    assert post(authenticated, '/auth/step-up', {'password': PASSWORD, 'code': pyotp.TOTP(SECRET).now()}).status_code == 401
    assert not db.scalar(select(Session.id).where(Session.level == 'full', Session.revoked_at.is_(None)))


def test_concurrent_factor_removal_preserves_a_factor(enrolled_user, db):
    from fastapi import Response
    from app.security.service import new_session, factor_count
    from http.cookies import SimpleCookie
    key = Credential(user_id=enrolled_user.id, credential_id='synthetic-key-id',
                     public_key='synthetic-public-key-unused', name='fixture')
    db.add(key)
    clients = []
    for _ in range(2):
        response = Response()
        session = new_session(db, response, enrolled_user, 'full')
        session.step_up_at = now()
        client = TestClient(app, base_url='http://localhost:5173')
        client.headers['Origin'] = 'http://localhost:5173'
        client.headers['X-CSRF-Token'] = session.csrf_token
        cookies = SimpleCookie()
        cookies.load(response.headers['set-cookie'])
        client.cookies.set('hub_session', cookies['hub_session'].value)
        clients.append(client)
    db.commit()
    paths = ['/security/totp', f'/security/keys/{key.id}']
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda index: post(clients[index], paths[index], method='DELETE').status_code, range(2)))
    assert sorted(statuses) == [200, 401]
    db.expire_all()
    assert factor_count(db, enrolled_user.id) == 1


def test_delete_payload_size_is_bounded_before_parsing(client):
    response = client.request('DELETE', '/api/v1/journal/entries/00000000-0000-0000-0000-000000000000',
                              content=b'x' * (2 * 1024 * 1024 + 1))
    assert response.status_code == 413
