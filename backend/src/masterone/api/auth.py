"""Email/password authentication and server-side browser sessions."""

import hashlib
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from masterone.auth.models import AuthSession, DemoGrant, LoginAttempt, User
from masterone.auth.security import (
    COOKIE,
    DEMO_COOKIE,
    LOGIN_WINDOW,
    SESSION_SECONDS,
    CurrentSession,
    capabilities_for_role,
    effective_capabilities,
    check_rate_limit,
    csrf_token,
    current_session,
    normalized_email,
    password_hash,
    require_mutation,
    require_origin,
)
from masterone.config import get_settings
from masterone.db.base import now_utc
from masterone.db.session import get_session

router = APIRouter(prefix="/auth", tags=["Authentication"])


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)


class AuthUserResponse(BaseModel):
    id: str
    email: str
    csrf_token: str
    role: str
    capabilities: list[str]
    demo: dict | None = None


def auth_user_response(user: User, token: str) -> AuthUserResponse:
    return AuthUserResponse(id=user.id, email=user.email, csrf_token=csrf_token(token),
                            role=user.role, capabilities=capabilities_for_role(user.role))


@router.post("/register", status_code=201)
def register(body: Credentials, request: Request, db: Session = Depends(get_session)):
    require_origin(request)
    email = normalized_email(str(body.email))
    user = User(email=str(body.email).strip(), email_normalized=email,
                password_hash=password_hash.hash(body.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Email already registered") from exc
    return {"id": user.id, "email": user.email}


@router.post("/login", response_model=AuthUserResponse)
def login(body: Credentials, request: Request, response: Response, db: Session = Depends(get_session)):
    require_origin(request)
    email = normalized_email(str(body.email))
    ip = request.client.host if request.client else "unknown"
    now = int(time.time())
    db.execute(delete(LoginAttempt).where(LoginAttempt.created_at_epoch < now - LOGIN_WINDOW))
    check_rate_limit(db, email, ip, now)
    user = db.scalar(select(User).where(User.email_normalized == email))
    valid = password_hash.verify(body.password, user.password_hash) if user else False
    if not valid or not user.is_active:
        db.add(LoginAttempt(email_normalized=email, ip_address=ip, created_at_epoch=now))
        db.commit()
        raise HTTPException(401, "Invalid email or password")
    demo_token = request.cookies.get(DEMO_COOKIE)
    if demo_token:
        old = db.scalar(select(AuthSession).where(AuthSession.token_hash == hashlib.sha256(demo_token.encode()).hexdigest()))
        if old:
            from masterone.mdm.demo_service import end_demo
            grant = db.get(DemoGrant, old.user_id)
            if grant: end_demo(db, grant)
    raw_token = secrets.token_urlsafe(32)
    db.add(AuthSession(user_id=user.id, token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
                       expires_at_epoch=now + SESSION_SECONDS))
    db.commit()
    response.set_cookie(COOKIE, raw_token, max_age=SESSION_SECONDS, httponly=True,
                        secure=get_settings().app_env == "production", samesite="lax", path="/")
    response.delete_cookie(DEMO_COOKIE, path='/', samesite='lax', secure=get_settings().app_env == 'production')
    return auth_user_response(user, raw_token)


@router.get("/me", response_model=AuthUserResponse)
def me(auth: CurrentSession = Depends(current_session)):
    result = auth_user_response(auth.user, auth.token)
    result.capabilities = effective_capabilities(auth)
    if auth.demo:
        result.demo = {'run_id': auth.demo.run_id, 'state': auth.demo.state,
                       'expires_at_epoch': auth.demo.expires_at_epoch, 'dataset_version': auth.demo.dataset_version}
    return result


@router.post('/demo', response_model=AuthUserResponse, status_code=201)
def enter_demo(request: Request, response: Response, db: Session = Depends(get_session)):
    require_origin(request)
    settings = get_settings()
    if not settings.demo_enabled:
        raise HTTPException(404, 'Chế độ demo chưa được bật.')
    now = int(time.time())
    # Serialize admission on PostgreSQL; no unbounded anonymous AI sessions.
    if db.get_bind().dialect.name == 'postgresql':
        from sqlalchemy import text
        db.execute(text('SELECT pg_advisory_xact_lock(1190035)'))
    ip = request.client.host if request.client else 'unknown'
    recent = db.query(LoginAttempt).filter_by(email_normalized='product-demo', ip_address=ip).filter(
        LoginAttempt.created_at_epoch > now - LOGIN_WINDOW).count()
    if recent >= 20:
        raise HTTPException(429, 'Bạn đã mở nhiều phiên demo liên tiếp. Vui lòng chờ một chút rồi thử lại.')
    # Re-entering the login page does not leak another slot/session.
    old_token = request.cookies.get(DEMO_COOKIE)
    if old_token:
        old = db.scalar(select(AuthSession).where(AuthSession.token_hash == hashlib.sha256(old_token.encode()).hexdigest()))
        if old:
            from masterone.mdm.demo_service import end_demo
            grant = db.get(DemoGrant, old.user_id)
            if grant: end_demo(db, grant)
    db.flush()
    active = db.query(DemoGrant).filter(DemoGrant.expires_at_epoch > now,
        DemoGrant.last_seen_epoch > now - settings.demo_idle_seconds,
        DemoGrant.state.in_(('NEW', 'STARTING', 'READY'))).count()
    if active >= settings.demo_max_active_sessions:
        raise HTTPException(429, 'Các phiên demo đang được sử dụng. Bạn chờ một chút rồi thử lại nhé.')
    identity = secrets.token_hex(16)
    user = User(email=f'demo-{identity}@example.test', email_normalized=f'demo-{identity}@example.test',
                password_hash=password_hash.hash(secrets.token_urlsafe(32)), role='STEWARD')
    db.add(user)
    db.flush()
    raw_token = secrets.token_urlsafe(32)
    session = AuthSession(user_id=user.id, token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
                          expires_at_epoch=now + settings.demo_session_seconds)
    db.add(session)
    db.flush()
    grant = DemoGrant(user_id=user.id, session_id=session.id, state='NEW',
        expires_at_epoch=session.expires_at_epoch, last_seen_epoch=now, created_at_epoch=now)
    db.add(grant)
    db.add(LoginAttempt(email_normalized='product-demo', ip_address=ip, created_at_epoch=now))
    db.commit()
    response.set_cookie(DEMO_COOKIE, raw_token, httponly=True, secure=settings.app_env == 'production',
                        samesite='lax', path='/')
    return me(CurrentSession(user=user, session=session, token=raw_token, demo=grant))


@router.post("/logout")
def logout(response: Response, auth: CurrentSession = Depends(require_mutation), db: Session = Depends(get_session)):
    auth.session.revoked_at = now_utc()
    if auth.demo:
        from masterone.mdm.demo_service import end_demo
        end_demo(db, db.get(DemoGrant, auth.user.id))
    db.commit()
    response.delete_cookie(DEMO_COOKIE if auth.demo else COOKIE, path="/", samesite="lax", secure=get_settings().app_env == "production")
    return {"ok": True}
