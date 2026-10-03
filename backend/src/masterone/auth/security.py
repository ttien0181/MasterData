"""Browser session, CSRF and login throttling helpers."""

import hashlib
import hmac
import time
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from pwdlib import PasswordHash
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from masterone.auth.models import AuthSession, LoginAttempt, User
from masterone.config import get_settings
from masterone.db.session import get_session

password_hash = PasswordHash.recommended()
COOKIE = "masterone_session"
SESSION_SECONDS = 7 * 24 * 60 * 60
LOGIN_WINDOW = 15 * 60


@dataclass
class CurrentSession:
    user: User
    session: AuthSession
    token: str


def allowed_origins() -> set[str]:
    return {origin.strip().rstrip("/") for origin in get_settings().cors_origins.split(",") if origin.strip()}


def require_origin(request: Request) -> None:
    if request.headers.get("origin") not in allowed_origins():
        raise HTTPException(403, "Origin is not allowed")


def csrf_token(raw_token: str) -> str:
    secret = get_settings().auth_csrf_secret
    if get_settings().app_env == "production" and len(secret) < 32:
        raise RuntimeError("AUTH_CSRF_SECRET must be at least 32 characters in production")
    return hmac.new((secret + raw_token).encode(), b"masterone-csrf-v1", hashlib.sha256).hexdigest()


def current_session(request: Request, db: Session = Depends(get_session)) -> CurrentSession:
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "Authentication required")
    digest = hashlib.sha256(token.encode()).hexdigest()
    auth_session = db.scalar(select(AuthSession).where(AuthSession.token_hash == digest))
    if not auth_session or auth_session.revoked_at or auth_session.expires_at_epoch <= int(time.time()):
        raise HTTPException(401, "Authentication required")
    user = db.get(User, auth_session.user_id)
    if not user or not user.is_active:
        raise HTTPException(401, "Authentication required")
    return CurrentSession(user=user, session=auth_session, token=token)


def require_mutation(request: Request, auth: CurrentSession = Depends(current_session)) -> CurrentSession:
    require_origin(request)
    header = request.headers.get("x-csrf-token", "")
    if not hmac.compare_digest(header, csrf_token(auth.token)):
        raise HTTPException(403, "Invalid CSRF token")
    return auth


# Thứ tự quyền tăng dần — role cao hơn bao gồm quyền của role thấp hơn
_ROLE_RANK: dict[str, int] = {
    "VIEWER": 0,
    "STEWARD": 1,
    "SENIOR_STEWARD": 2,
    "POLICY_ADMIN": 3,
    "AUDITOR": 3,
}


def require_role(minimum_role: str):
    """FastAPI dependency factory.  Dùng: Depends(require_role("STEWARD"))."""
    min_rank = _ROLE_RANK.get(minimum_role, 99)

    def _check(auth: CurrentSession = Depends(current_session)) -> CurrentSession:
        user_rank = _ROLE_RANK.get(getattr(auth.user, "role", "VIEWER"), 0)
        if user_rank < min_rank:
            raise HTTPException(403, f"Cần quyền {minimum_role} trở lên")
        return auth

    return _check


def normalized_email(email: str) -> str:
    return email.strip().casefold()


def check_rate_limit(db: Session, email: str, ip: str, now: int) -> None:
    recent = LoginAttempt.created_at_epoch >= now - LOGIN_WINDOW
    for condition, threshold in ((LoginAttempt.ip_address == ip, 20),
                                 ((LoginAttempt.ip_address == ip) & (LoginAttempt.email_normalized == email), 5)):
        count = db.scalar(select(func.count()).select_from(LoginAttempt).where(recent, condition))
        if count >= threshold:
            raise HTTPException(429, "Too many login attempts. Try again later", headers={"Retry-After": str(LOGIN_WINDOW)})
