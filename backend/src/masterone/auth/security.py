"""Browser session, CSRF and login throttling helpers."""

import hashlib
import hmac
import re
import time
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from pwdlib import PasswordHash
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from masterone.auth.models import AuthSession, LoginAttempt, User
from masterone.config import LOCAL_DEV_ORIGIN_REGEX, get_settings
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
    origin = request.headers.get("origin", "")
    if origin in allowed_origins():
        return
    if get_settings().app_env == "development" and re.fullmatch(LOCAL_DEV_ORIGIN_REGEX, origin):
        return
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


# ── RBAC (§26.1) ─────────────────────────────────────────────────────────────

ROLES = ("VIEWER", "STEWARD", "SENIOR_STEWARD", "POLICY_ADMIN", "AUDITOR")
_ROLE_RANK: dict[str, int] = {r: i for i, r in enumerate(ROLES)}

# Operational permissions are explicit: an auditor/policy admin is NOT a steward.
_READ_CAPABILITIES = frozenset({"read"})
_AUDIT_CAPABILITIES = _READ_CAPABILITIES | frozenset({"read_audit"})
_STEWARD_CAPABILITIES = _AUDIT_CAPABILITIES | frozenset({
    "create_run", "start_run", "approve_pair", "reject_pair", "edit_golden",
    "explain", "export_data", "manage_run",
    "learn_match_profile", "apply_match_profile", "manage_entity_structure",
})
ROLE_CAPABILITIES: dict[str, frozenset[str]] = {
    "VIEWER": _READ_CAPABILITIES,
    "STEWARD": _STEWARD_CAPABILITIES,
    "SENIOR_STEWARD": _STEWARD_CAPABILITIES | {"approve_d_subgroup", "selective_unmerge"},
    "POLICY_ADMIN": _AUDIT_CAPABILITIES | {"edit_policy", "simulate_policy"},
    "AUDITOR": _AUDIT_CAPABILITIES,
}

def capabilities_for_role(role: str) -> list[str]:
    """Stable public contract; unknown roles have no permissions."""
    return sorted(ROLE_CAPABILITIES.get(role, frozenset()))


def check_capability(auth: CurrentSession, capability: str) -> None:
    if capability not in ROLE_CAPABILITIES.get(auth.user.role, frozenset()):
        raise HTTPException(403, f"Capability '{capability}' required")


def require_role(min_role: str):
    """Legacy rank helper. Product endpoints must use explicit capabilities."""
    if min_role not in _ROLE_RANK:
        raise ValueError(f"Unknown role: {min_role}")
    def _dep(auth: CurrentSession = Depends(current_session)) -> CurrentSession:
        user_rank = _ROLE_RANK.get(auth.user.role, -1)
        min_rank  = _ROLE_RANK.get(min_role, 0)
        if user_rank < min_rank:
            raise HTTPException(403, f"Role '{min_role}' or higher required")
        return auth
    return _dep


def require_capability(capability: str):
    """FastAPI dependency factory — checks that the user has a capability."""
    def _dep(auth: CurrentSession = Depends(current_session)) -> CurrentSession:
        check_capability(auth, capability)
        return auth
    return _dep


def require_mutation_capability(capability: str):
    """Preserve cookie, Origin and CSRF validation before authorizing a write."""
    def _dep(auth: CurrentSession = Depends(require_mutation)) -> CurrentSession:
        check_capability(auth, capability)
        return auth
    return _dep


def normalized_email(email: str) -> str:
    return email.strip().casefold()


def check_rate_limit(db: Session, email: str, ip: str, now: int) -> None:
    recent = LoginAttempt.created_at_epoch >= now - LOGIN_WINDOW
    for condition, threshold in ((LoginAttempt.ip_address == ip, 20),
                                 ((LoginAttempt.ip_address == ip) & (LoginAttempt.email_normalized == email), 5)):
        count = db.scalar(select(func.count()).select_from(LoginAttempt).where(recent, condition))
        if count >= threshold:
            raise HTTPException(429, "Too many login attempts. Try again later", headers={"Retry-After": str(LOGIN_WINDOW)})
