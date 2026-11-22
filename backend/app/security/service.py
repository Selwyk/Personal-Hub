import base64
import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta, timezone

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from cryptography.fernet import Fernet
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select, update, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session as DbSession

from app.core.config import settings
from app.database.models import (
    User, Session, Device, TotpFactor, Credential, Challenge, RecoveryCode,
    CollectorToken, SecurityEvent, RateLimit,
)
from app.database.session import get_db

password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
dummy_hash = password_hasher.hash(secrets.token_urlsafe(32))


def now():
    return datetime.now(timezone.utc)


def digest(value: str):
    return hashlib.sha256(value.encode()).hexdigest()


def encode(value: bytes):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def decode(value: str):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def encrypt(value: str):
    return Fernet(settings().totp_encryption_key.encode()).encrypt(value.encode()).decode()


def decrypt(value: str):
    return Fernet(settings().totp_encryption_key.encode()).decrypt(value.encode()).decode()


def verify_password(hashed, supplied):
    try:
        return password_hasher.verify(hashed, supplied)
    except (VerificationError, InvalidHashError):
        return False


def event(db, kind, user_id=None, **details):
    db.add(SecurityEvent(kind=kind, user_id=user_id, details=details))


def throttle(db, request, operation, user_id=None, limit=10):
    # Atomic fixed windows shared by all workers. Persist even on failed authentication.
    stamp = now()
    bucket = int(stamp.timestamp()) // 300
    keys = [f"{operation}:ip:{request.client.host if request.client else 'unknown'}:{bucket}"]
    if user_id:
        keys.append(f"{operation}:user:{user_id}:{bucket}")
    for raw in keys:
        statement = insert(RateLimit).values(key=digest(raw), count=1, window_at=stamp)
        statement = statement.on_conflict_do_update(
            index_elements=[RateLimit.key], set_={"count": RateLimit.count + 1})
        count = db.scalar(statement.returning(RateLimit.count))
        if count > limit:
            event(db, "authentication_rate_limited", user_id)
            db.commit()
            raise HTTPException(429, "Demasiados intentos; vuelve a intentarlo más tarde.",
                                headers={"Retry-After": str(300 - int(stamp.timestamp()) % 300)})
    db.commit()


def set_cookie(response, name, value, max_age):
    response.set_cookie(name, value, max_age=max_age, httponly=True,
                        secure=settings().cookie_secure, samesite="strict", path="/")


def new_session(db, response, user=None, level="anonymous", old=None):
    stamp = now()
    if old:
        old.revoked_at = stamp
    raw = secrets.token_urlsafe(32)
    ttl = settings().session_absolute_timeout_seconds if level == "full" else 600
    session = Session(token_hash=digest(raw), csrf_token=secrets.token_urlsafe(32),
                      user_id=user.id if user else None, level=level,
                      security_version=user.security_version if user else 0,
                      last_seen_at=stamp, expires_at=stamp + timedelta(seconds=ttl))
    db.add(session)
    db.flush()
    set_cookie(response, settings().session_cookie, raw, ttl)
    event(db, "session_created", user.id if user else None, level=level)
    return session


def get_session(request: Request, db: DbSession = Depends(get_db, scope="function")):
    raw = request.cookies.get(settings().session_cookie, "")
    session = db.scalar(select(Session).where(Session.token_hash == digest(raw))) if raw else None
    # Single-user app: lock the principal before its session everywhere. This
    # serializes factor changes across browsers and avoids inverse lock ordering
    # while revoking all sessions, preserving the last-factor invariant.
    user = db.scalar(select(User).where(User.id == session.user_id).with_for_update()
                     .execution_options(populate_existing=True)) if session and session.user_id else None
    if session:
        db.refresh(session, with_for_update=True)
    stamp = now()
    if not session or session.revoked_at or session.expires_at <= stamp:
        raise HTTPException(401, "Sesión no válida o caducada.")
    if session.last_seen_at + timedelta(seconds=settings().session_idle_timeout_seconds) <= stamp:
        raise HTTPException(401, "Sesión no válida o caducada.")
    if session.user_id:
        if not user or user.security_version != session.security_version:
            raise HTTPException(401, "Sesión no válida o caducada.")
    if session.device_id:
        device = db.get(Device, session.device_id)
        if not device or device.revoked_at or device.expires_at <= stamp:
            raise HTTPException(401, "Es necesario autenticar de nuevo.")
    session.last_seen_at = stamp
    return session


def csrf(request: Request, session: Session = Depends(get_session)):
    origin = request.headers.get("origin", "")
    token = request.headers.get("x-csrf-token", "")
    if origin not in settings().origins or not hmac.compare_digest(session.csrf_token, token):
        raise HTTPException(403, "No se ha podido validar la petición.")


def signed_in(session: Session = Depends(get_session)):
    if session.level != "full":
        raise HTTPException(403, "Completa la autenticación de segundo factor.")
    return session


def sensitive(session: Session = Depends(signed_in)):
    if not session.step_up_at or session.step_up_at + timedelta(minutes=5) <= now():
        raise HTTPException(403, "Confirma contraseña y segundo factor para continuar.")
    return session


def factor_count(db, user_id):
    totp = db.scalar(select(TotpFactor).where(TotpFactor.user_id == user_id, TotpFactor.active))
    keys = db.scalars(select(Credential).where(Credential.user_id == user_id,
                                             Credential.revoked_at.is_(None))).all()
    return int(totp is not None) + len(keys)


def enrollment_allowed(db, session):
    if session.level in ("enrollment", "recovery"):
        return
    sensitive(session)


def invalidate_user(db, user):
    stamp = now()
    user.security_version += 1
    db.execute(update(Session).where(Session.user_id == user.id, Session.revoked_at.is_(None))
               .values(revoked_at=stamp))
    db.execute(update(Device).where(Device.user_id == user.id, Device.revoked_at.is_(None))
               .values(revoked_at=stamp))
    db.execute(update(Challenge).where(Challenge.session_id.in_(
        select(Session.id).where(Session.user_id == user.id))).values(consumed_at=stamp))


def generate_recovery(db, user_id):
    db.execute(update(RecoveryCode).where(RecoveryCode.user_id == user_id,
                                         RecoveryCode.used_at.is_(None)).values(used_at=now()))
    codes = [secrets.token_hex(16) for _ in range(10)]
    db.add_all([RecoveryCode(user_id=user_id, code_hash=digest(code)) for code in codes])
    event(db, "recovery_codes_generated", user_id)
    return codes


def complete_mfa(db, response, session, trust=False, name="Navegador", enrolling=False, step_up=False):
    user = db.get(User, session.user_id)
    if not enrolling:
        current_session(db, session)
        db.refresh(user)
    fresh = new_session(db, response, user, "full", session)
    fresh.mfa_at = now()
    # A password login is only recent while the short pre-MFA session remains valid.
    fresh.step_up_at = now() if step_up or session.created_at + timedelta(minutes=5) > now() else None
    if trust:
        raw = secrets.token_urlsafe(32)
        device = Device(user_id=user.id, token_hash=digest(raw), name=name,
                        expires_at=now() + timedelta(days=settings().trusted_device_days),
                        security_version=user.security_version)
        db.add(device)
        db.flush()
        fresh.device_id = device.id
        set_cookie(response, settings().device_cookie, raw, settings().trusted_device_days * 86400)
    event(db, "mfa_completed", user.id)
    return {"level": "full", "csrf_token": fresh.csrf_token}


def verify_totp(db, user_id, code):
    factor = db.scalar(select(TotpFactor).where(TotpFactor.user_id == user_id,
                                              TotpFactor.active).with_for_update())
    if not factor or not code.isdigit() or len(code) != 6:
        return False
    secret = decrypt(factor.secret_encrypted)
    current = int(time.time()) // 30
    for step in (current, current - 1, current + 1):
        if step > factor.last_step and hmac.compare_digest(pyotp.TOTP(secret).at(step * 30), code):
            factor.last_step = step
            db.flush()
            return True
    return False


def consume_challenge(db, session, challenge_id, purpose):
    challenge = db.scalar(select(Challenge).where(Challenge.id == challenge_id,
        Challenge.session_id == session.id, Challenge.purpose == purpose).with_for_update())
    if not challenge or challenge.consumed_at or challenge.expires_at <= now():
        raise HTTPException(400, "Comprobación no válida o caducada.")
    challenge.consumed_at = now()
    # Failed verifications must also consume the challenge.
    db.commit()
    # Persistent attempt/challenge commits release row locks. Recheck authority
    # before enrolling a factor or issuing a session after concurrent revocation.
    current_session(db, session)
    return challenge


def current_session(db, session):
    user = db.scalar(select(User).where(User.id == session.user_id).with_for_update()
                     .execution_options(populate_existing=True)) if session.user_id else None
    db.refresh(session, with_for_update=True)
    if session.revoked_at or session.expires_at <= now() or (user and session.security_version != user.security_version):
        raise HTTPException(401, "Sesión no válida o caducada.")


def add_challenge(db, session, purpose, value):
    db.execute(update(Challenge).where(Challenge.session_id == session.id,
        Challenge.purpose == purpose, Challenge.consumed_at.is_(None)).values(consumed_at=now()))
    challenge = Challenge(session_id=session.id, purpose=purpose, value=value,
                          expires_at=now() + timedelta(minutes=5))
    db.add(challenge)
    db.flush()
    return challenge


def invalidate_after_restore(db):
    stamp = now()
    counts = {}
    for model, field, name in ((Session, "revoked_at", "sessions"),
                               (Challenge, "consumed_at", "challenges"),
                               (Device, "revoked_at", "devices"),
                               (CollectorToken, "revoked_at", "collector_tokens")):
        column = getattr(model, field)
        result = db.execute(update(model).where(column.is_(None)).values({field: stamp}))
        counts[name] = result.rowcount
    if any(counts.values()):
        db.execute(update(User).values(security_version=User.security_version + 1))
    event(db, "restore_credentials_invalidated", **counts)
    return counts


def verify_after_restore(db):
    counts = {name: db.scalar(select(func.count()).select_from(model).where(getattr(model, field).is_(None)))
              for model, field, name in ((Session, "revoked_at", "sessions"),
                                        (Challenge, "consumed_at", "challenges"),
                                        (Device, "revoked_at", "devices"),
                                        (CollectorToken, "revoked_at", "collector_tokens"))}
    return {"valid": not any(counts.values()), "active": counts}
