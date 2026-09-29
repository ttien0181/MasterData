"""Email/password authentication and server-side browser sessions."""

import hashlib
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from masterone.auth.models import AuthSession, LoginAttempt, User
from masterone.auth.security import (
    COOKIE,
    LOGIN_WINDOW,
    SESSION_SECONDS,
    CurrentSession,
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


@router.post("/login")
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
    raw_token = secrets.token_urlsafe(32)
    db.add(AuthSession(user_id=user.id, token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
                       expires_at_epoch=now + SESSION_SECONDS))
    db.commit()
    response.set_cookie(COOKIE, raw_token, max_age=SESSION_SECONDS, httponly=True,
                        secure=get_settings().app_env == "production", samesite="lax", path="/")
    return {"id": user.id, "email": user.email, "csrf_token": csrf_token(raw_token)}


@router.get("/me")
def me(auth: CurrentSession = Depends(current_session)):
    return {"id": auth.user.id, "email": auth.user.email, "csrf_token": csrf_token(auth.token)}


@router.post("/logout")
def logout(response: Response, auth: CurrentSession = Depends(require_mutation), db: Session = Depends(get_session)):
    auth.session.revoked_at = now_utc()
    db.commit()
    response.delete_cookie(COOKIE, path="/", samesite="lax", secure=get_settings().app_env == "production")
    return {"ok": True}
