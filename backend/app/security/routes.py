import io
import json
from uuid import UUID

import pyotp
import qrcode
import qrcode.image.svg
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session as DbSession
from webauthn import (generate_authentication_options, generate_registration_options,
                      verify_authentication_response, verify_registration_response, options_to_json)
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (AuthenticatorSelectionCriteria,
                                     UserVerificationRequirement, PublicKeyCredentialDescriptor)

from app.core.config import settings
from app.database.models import (User, Session, Device, TotpFactor, Credential,
                                 RecoveryCode, CollectorToken)
from app.database.session import get_db
from app.security import schemas as schema
from app.security.service import (now, digest, encode, decode, encrypt, decrypt,
    verify_password, password_hasher, dummy_hash, throttle, event, new_session, get_session,
    signed_in, sensitive, factor_count, enrollment_allowed, invalidate_user, generate_recovery,
    complete_mfa, verify_totp, add_challenge, consume_challenge, current_session)
import secrets

auth = APIRouter(prefix="/auth", tags=["authentication"])
security = APIRouter(prefix="/security", tags=["security"])


def failure(db, user_id=None):
    event(db, "authentication_failed", user_id)
    db.commit()
    raise HTTPException(401, "No se ha podido completar la autenticación.")


def password_stage(session):
    if session.level != "password":
        raise HTTPException(403, "Introduce primero la contraseña.")


@auth.get("/csrf")
def csrf_token(request: Request, response: Response, db: DbSession = Depends(get_db, scope="function")):
    try:
        session = get_session(request, db)
    except HTTPException:
        throttle(db, request, "anonymous_session", limit=100)
        session = new_session(db, response)
    response.headers["Cache-Control"] = "no-store"
    return {"csrf_token": session.csrf_token, "level": session.level}


@auth.get("/me")
def me(session: Session = Depends(get_session), db: DbSession = Depends(get_db, scope="function")):
    user = db.get(User, session.user_id) if session.user_id else None
    return {"level": session.level, "username": user.username if user else None,
            "has_totp": bool(user and db.scalar(select(TotpFactor.id).where(
                TotpFactor.user_id == user.id, TotpFactor.active))),
            "has_webauthn": bool(user and db.scalar(select(Credential.id).where(
                Credential.user_id == user.id, Credential.revoked_at.is_(None))))}


@auth.post("/login")
def login(body: schema.Login, request: Request, response: Response,
          session: Session = Depends(get_session), db: DbSession = Depends(get_db, scope="function")):
    user = db.scalar(select(User).where(User.username == body.username))
    throttle(db, request, "password", user.id if user else None)
    if not verify_password(user.password_hash if user else dummy_hash, body.password) or not user:
        failure(db)
    if password_hasher.check_needs_rehash(user.password_hash):
        user.password_hash = password_hasher.hash(body.password)
    level = "password" if user.mfa_initialized or factor_count(db, user.id) else "enrollment"
    if user.must_change_password:
        level = "initial_password"
    device = None
    raw = request.cookies.get(settings().device_cookie)
    if raw and level == "password":
        device = db.scalar(select(Device).where(Device.token_hash == digest(raw),
            Device.user_id == user.id, Device.revoked_at.is_(None), Device.expires_at > now(),
            Device.security_version == user.security_version))
        if device:
            level = "full"
            event(db, "trusted_device_login", user.id)
    fresh = new_session(db, response, user, level, session)
    if device:
        fresh.device_id = device.id
    event(db, "password_verified", user.id)
    return {"level": fresh.level, "csrf_token": fresh.csrf_token}


@auth.post("/initial-password")
def initial_password(body: schema.PasswordChange, response: Response,
                     session: Session = Depends(get_session),
                     db: DbSession = Depends(get_db, scope="function")):
    user = db.get(User, session.user_id) if session.user_id else None
    if session.level != "initial_password" or not user or not user.must_change_password:
        raise HTTPException(403, "Esta operación solo está disponible durante el alta inicial.")
    if verify_password(user.password_hash, body.new_password):
        raise HTTPException(422, "Elige una contraseña distinta de la temporal.")
    user.password_hash = password_hasher.hash(body.new_password)
    user.must_change_password = False
    invalidate_user(db, user)
    fresh = new_session(db, response, user, "enrollment", session)
    event(db, "initial_password_changed", user.id)
    return {"level": fresh.level, "csrf_token": fresh.csrf_token}


@auth.post("/logout")
def logout(response: Response, session: Session = Depends(get_session),
           db: DbSession = Depends(get_db, scope="function")):
    session.revoked_at = now()
    response.delete_cookie(settings().session_cookie, path="/", secure=settings().cookie_secure,
                           httponly=True, samesite="strict")
    event(db, "session_logged_out", session.user_id)
    return {"ok": True}


@auth.post("/totp/setup")
def totp_setup(session: Session = Depends(get_session), db: DbSession = Depends(get_db, scope="function")):
    enrollment_allowed(db, session)
    user = db.get(User, session.user_id)
    if db.scalar(select(TotpFactor).where(TotpFactor.user_id == user.id, TotpFactor.active)):
        raise HTTPException(409, "Ya hay un factor TOTP activo.")
    secret = pyotp.random_base32()
    challenge = add_challenge(db, session, "totp_enroll", encrypt(secret))
    uri = pyotp.TOTP(secret).provisioning_uri(user.username, issuer_name="Personal Hub")
    image = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage)
    stream = io.BytesIO()
    image.save(stream)
    return {"challenge_id": str(challenge.id), "secret": secret, "uri": uri,
            "qr_svg": stream.getvalue().decode()}


def enrolled(db, response, session):
    user = db.get(User, session.user_id)
    initial = session.level in ("enrollment", "recovery")
    user.mfa_initialized = True
    invalidate_user(db, user)
    codes = generate_recovery(db, user.id) if initial else None
    event(db, "factor_changed", user.id)
    if initial:
        result = complete_mfa(db, response, session, trust=False, enrolling=True)
        result["recovery_codes"] = codes
        return result
    response.delete_cookie(settings().session_cookie, path="/")
    return {"login_required": True}


@auth.post("/totp/confirm")
def totp_confirm(body: schema.ChallengeCode, request: Request, response: Response,
                 session: Session = Depends(get_session), db: DbSession = Depends(get_db, scope="function")):
    enrollment_allowed(db, session)
    throttle(db, request, "totp_enroll", session.user_id)
    challenge = consume_challenge(db, session, body.challenge_id, "totp_enroll")
    secret = decrypt(challenge.value)
    if not pyotp.TOTP(secret).verify(body.code, valid_window=1):
        failure(db, session.user_id)
    step = next(s for s in (int(now().timestamp()) // 30,
                           int(now().timestamp()) // 30 - 1,
                           int(now().timestamp()) // 30 + 1)
                if pyotp.TOTP(secret).at(s * 30) == body.code)
    factor = db.scalar(select(TotpFactor).where(TotpFactor.user_id == session.user_id)
                       .with_for_update())
    if not factor:
        factor = TotpFactor(user_id=session.user_id)
        db.add(factor)
    elif factor.active:
        raise HTTPException(409, "Ya hay un factor TOTP activo.")
    factor.secret_encrypted = encrypt(secret)
    factor.last_step = step
    factor.active = True
    db.flush()
    return enrolled(db, response, session)


@auth.post("/totp/verify")
def totp_login(body: schema.TotpCode, request: Request, response: Response,
               session: Session = Depends(get_session), db: DbSession = Depends(get_db, scope="function")):
    password_stage(session)
    throttle(db, request, "mfa", session.user_id)
    if not verify_totp(db, session.user_id, body.code):
        failure(db, session.user_id)
    return complete_mfa(db, response, session, body.trust_device, body.device_name)


@auth.post("/step-up")
def step_up(body: schema.StepUp, request: Request, response: Response,
            session: Session = Depends(signed_in), db: DbSession = Depends(get_db, scope="function")):
    throttle(db, request, "step_up", session.user_id)
    current_session(db, session)
    user = db.get(User, session.user_id)
    if not verify_password(user.password_hash, body.password) or not verify_totp(db, user.id, body.code):
        failure(db, user.id)
    fresh = new_session(db, response, user, "full", session)
    fresh.mfa_at = fresh.step_up_at = now()
    return {"csrf_token": fresh.csrf_token, "level": "full"}


@auth.post("/webauthn/register/options")
def key_register_options(session: Session = Depends(get_session), db: DbSession = Depends(get_db, scope="function")):
    enrollment_allowed(db, session)
    user = db.get(User, session.user_id)
    options = generate_registration_options(rp_id=settings().webauthn_rp_id, rp_name="Personal Hub",
        user_name=user.username, user_id=user.id.bytes,
        authenticator_selection=AuthenticatorSelectionCriteria(
            user_verification=UserVerificationRequirement.REQUIRED),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=decode(key.credential_id))
            for key in db.scalars(select(Credential).where(Credential.user_id == user.id))])
    challenge = add_challenge(db, session, "key_register", encode(options.challenge))
    return {"challenge_id": str(challenge.id), "options": json.loads(options_to_json(options))}


@auth.post("/webauthn/register/verify")
def key_register(body: schema.KeyResult, request: Request, response: Response,
                 session: Session = Depends(get_session), db: DbSession = Depends(get_db, scope="function")):
    enrollment_allowed(db, session)
    throttle(db, request, "key_register", session.user_id)
    challenge = consume_challenge(db, session, body.challenge_id, "key_register")
    try:
        verified = verify_registration_response(credential=body.credential,
            expected_challenge=decode(challenge.value), expected_rp_id=settings().webauthn_rp_id,
            expected_origin=settings().origins, require_user_verification=True)
    except (WebAuthnException, ValueError, KeyError, TypeError):
        failure(db, session.user_id)
    credential_id = encode(verified.credential_id)
    if db.scalar(select(Credential.id).where(Credential.credential_id == credential_id)):
        raise HTTPException(409, "La llave ya está registrada.")
    db.add(Credential(user_id=session.user_id, credential_id=credential_id,
        public_key=encode(verified.credential_public_key), sign_count=verified.sign_count,
        name=body.name, transports=body.credential.get("response", {}).get("transports", []),
        backed_up=verified.credential_backed_up))
    db.flush()
    return enrolled(db, response, session)


@auth.post("/webauthn/authenticate/options")
def key_auth_options(session: Session = Depends(get_session), db: DbSession = Depends(get_db, scope="function")):
    if session.level not in ("password", "full"):
        raise HTTPException(403, "Introduce primero la contraseña.")
    keys = db.scalars(select(Credential).where(Credential.user_id == session.user_id,
                                             Credential.revoked_at.is_(None))).all()
    if not keys:
        raise HTTPException(400, "No hay llaves disponibles.")
    options = generate_authentication_options(rp_id=settings().webauthn_rp_id,
        allow_credentials=[PublicKeyCredentialDescriptor(id=decode(key.credential_id)) for key in keys],
        user_verification=UserVerificationRequirement.REQUIRED)
    purpose = "key_step_up" if session.level == "full" else "key_login"
    challenge = add_challenge(db, session, purpose, encode(options.challenge))
    return {"challenge_id": str(challenge.id), "options": json.loads(options_to_json(options))}


@auth.post("/webauthn/authenticate/verify")
def key_auth(body: schema.KeyAuth, request: Request, response: Response,
             session: Session = Depends(get_session), db: DbSession = Depends(get_db, scope="function")):
    if session.level not in ("password", "full"):
        raise HTTPException(403, "Introduce primero la contraseña.")
    throttle(db, request, "mfa", session.user_id)
    purpose = "key_step_up" if session.level == "full" else "key_login"
    challenge = consume_challenge(db, session, body.challenge_id, purpose)
    user = db.get(User, session.user_id)
    if purpose == "key_step_up" and not verify_password(user.password_hash, body.password or ""):
        failure(db, user.id)
    key = db.scalar(select(Credential).where(
        Credential.credential_id == body.credential.get("id", ""),
        Credential.user_id == user.id, Credential.revoked_at.is_(None)).with_for_update())
    if not key:
        failure(db, user.id)
    try:
        verified = verify_authentication_response(credential=body.credential,
            expected_challenge=decode(challenge.value), expected_rp_id=settings().webauthn_rp_id,
            expected_origin=settings().origins, credential_public_key=decode(key.public_key),
            credential_current_sign_count=key.sign_count, require_user_verification=True)
    except (WebAuthnException, ValueError, KeyError, TypeError):
        failure(db, user.id)
    key.sign_count = verified.new_sign_count
    key.backed_up = verified.credential_backed_up
    result = complete_mfa(db, response, session,
        body.trust_device if purpose == "key_login" else False, body.device_name,
        step_up=purpose == "key_step_up")
    return result


@auth.post("/recover")
def recover(body: schema.TotpCode, request: Request, response: Response,
            session: Session = Depends(get_session), db: DbSession = Depends(get_db, scope="function")):
    password_stage(session)
    throttle(db, request, "recovery", session.user_id, limit=5)
    current_session(db, session)
    code = db.scalar(select(RecoveryCode).where(RecoveryCode.user_id == session.user_id,
        RecoveryCode.code_hash == digest(body.code), RecoveryCode.used_at.is_(None)).with_for_update())
    if not code:
        failure(db, session.user_id)
    code.used_at = now()
    user = db.get(User, session.user_id)
    invalidate_user(db, user)
    # Lost factors are replaced in this restricted session; no diary access or trust bypass.
    db.execute(update(TotpFactor).where(TotpFactor.user_id == user.id).values(active=False))
    db.execute(update(Credential).where(Credential.user_id == user.id).values(revoked_at=now()))
    fresh = new_session(db, response, user, "recovery", session)
    event(db, "recovery_started", user.id)
    return {"level": "recovery", "csrf_token": fresh.csrf_token}


@security.get("/overview")
def overview(session: Session = Depends(signed_in), db: DbSession = Depends(get_db, scope="function")):
    def rows(model, fields):
        return [{f: str(getattr(row, f)) if getattr(row, f) is not None else None for f in fields}
                for row in db.scalars(select(model).where(model.user_id == session.user_id)
                                     .order_by(model.created_at.desc())).all()]
    return {"sessions": rows(Session, ["id", "created_at", "last_seen_at", "expires_at", "revoked_at"]),
            "devices": rows(Device, ["id", "name", "created_at", "expires_at", "revoked_at"]),
            "keys": rows(Credential, ["id", "name", "created_at", "revoked_at"]),
            "tokens": rows(CollectorToken, ["id", "name", "created_at", "last_used_at", "expires_at", "revoked_at"]),
            "totp": bool(db.scalar(select(TotpFactor.id).where(TotpFactor.user_id == session.user_id,
                                                             TotpFactor.active))),
            "current_session_id": str(session.id)}


@security.post("/password")
def change_password(body: schema.PasswordChange, response: Response,
                    session: Session = Depends(sensitive), db: DbSession = Depends(get_db, scope="function")):
    user = db.get(User, session.user_id)
    user.password_hash = password_hasher.hash(body.new_password)
    invalidate_user(db, user)
    event(db, "password_changed", user.id)
    response.delete_cookie(settings().session_cookie, path="/")
    return {"login_required": True}


@security.post("/recovery-codes")
def recovery_codes(session: Session = Depends(sensitive), db: DbSession = Depends(get_db, scope="function")):
    return {"recovery_codes": generate_recovery(db, session.user_id)}


@security.delete("/totp")
def revoke_totp(response: Response, session: Session = Depends(sensitive),
                db: DbSession = Depends(get_db, scope="function")):
    factor = db.scalar(select(TotpFactor).where(TotpFactor.user_id == session.user_id,
                                              TotpFactor.active).with_for_update())
    if not factor:
        raise HTTPException(404, "Factor no encontrado.")
    if factor_count(db, session.user_id) <= 1:
        raise HTTPException(409, "Configura otro factor antes de eliminar el último.")
    factor.active = False
    return enrolled(db, response, session)


@security.patch("/keys/{key_id}")
def rename_key(key_id: UUID, body: schema.Named, session: Session = Depends(sensitive),
               db: DbSession = Depends(get_db, scope="function")):
    key = db.scalar(select(Credential).where(Credential.id == key_id,
                                            Credential.user_id == session.user_id))
    if not key:
        raise HTTPException(404, "Llave no encontrada.")
    key.name = body.name
    return {"ok": True}


@security.post("/tokens")
def create_token(body: schema.TokenCreate, session: Session = Depends(sensitive),
                 db: DbSession = Depends(get_db, scope="function")):
    if body.expires_at and (body.expires_at.tzinfo is None or body.expires_at <= now()):
        raise HTTPException(422, "La expiración debe ser futura e incluir zona horaria.")
    raw = secrets.token_urlsafe(32)
    token = CollectorToken(user_id=session.user_id, token_hash=digest(raw), name=body.name,
                           expires_at=body.expires_at)
    db.add(token)
    db.flush()
    event(db, "collector_token_created", session.user_id)
    return {"id": str(token.id), "token": raw, "scope": token.scope}


@security.delete("/{resource}/{resource_id}")
def revoke_resource(resource: str, resource_id: UUID, response: Response,
                    session: Session = Depends(sensitive), db: DbSession = Depends(get_db, scope="function")):
    models = {"sessions": Session, "devices": Device, "keys": Credential, "tokens": CollectorToken}
    model = models.get(resource)
    if not model:
        raise HTTPException(404, "Recurso no encontrado.")
    row = db.scalar(select(model).where(model.id == resource_id,
        model.user_id == session.user_id).with_for_update())
    if not row:
        raise HTTPException(404, "Recurso no encontrado.")
    if resource == "keys" and not row.revoked_at:
        if factor_count(db, session.user_id) <= 1:
            raise HTTPException(409, "Configura otro factor antes de eliminar el último.")
        row.revoked_at = now()
        return enrolled(db, response, session)
    row.revoked_at = row.revoked_at or now()
    if resource == "devices":
        db.execute(update(Session).where(Session.device_id == row.id).values(revoked_at=now()))
    event(db, "resource_revoked", session.user_id, resource=resource)
    return {"ok": True}
