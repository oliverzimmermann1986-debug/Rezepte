"""Native-App-Authentifizierung mit widerrufbaren Bearer-Sitzungen."""
from __future__ import annotations

import re
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from ..auth import (
    ROLE_ADMIN,
    ROLE_GUEST,
    ROLE_USER,
    GUEST_MAX_AGE,
    check_credentials,
    create_guest_session,
    create_session,
    guest_access_payload,
    request_is_guest,
    hash_password,
    request_user,
    revoke_current_session,
    validate_new_password,
)
from ..db import get_db
from ..security import (LoginRateLimiter, client_ip, login_actor_key, login_limiter,
                        login_ip_limiter)

router = APIRouter(prefix="/api/auth", tags=["auth"])
registration_limiter = LoginRateLimiter(max_fails=10, window_sec=600, ban_sec=600)
guest_limiter = LoginRateLimiter(max_fails=30, window_sec=300, ban_sec=300)


def _access_payload(username: str, *, read_only: bool = False) -> dict:
    """Einheitlicher Rollenvertrag für Login und Sitzungsprüfung.

    Ein noch nicht in die Datenbank migrierter Legacy-Config-Benutzer ist der
    bestehende Betreiber und bleibt deshalb Administrator. Reguläre DB-Konten
    werden ausschließlich anhand ihrer gespeicherten Rolle ausgewertet.
    """
    user = None
    if read_only:
        role = ROLE_GUEST
    else:
        user = get_db().user_get_by_name(username)
        role = (user or {}).get("role") or ROLE_ADMIN
        if role not in {ROLE_USER, ROLE_ADMIN}:
            role = ROLE_USER
    is_admin = role == ROLE_ADMIN
    return {
        "id": (user or {}).get("id"),
        "password_enabled": bool((user or {}).get("password_hash")),
        "username": username,
        "role": role,
        "is_admin": is_admin,
        "full_access": is_admin,
        "read_only": read_only,
    }


class NativeLogin(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=512)


class Registration(BaseModel):
    username: str = Field(min_length=3, max_length=60)
    password: str = Field(min_length=10, max_length=72)
    invitation_token: str = Field(default="", max_length=256)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value):
        value = value.strip()
        if not re.fullmatch(r"[\w.@+-]{3,60}", value):
            raise ValueError("Benutzername: 3–60 Buchstaben, Ziffern oder . @ + - _")
        return value

    @field_validator("password")
    @classmethod
    def validate_password(cls, value):
        return validate_new_password(value)


@router.post("/register", status_code=201)
def register_account(payload: Registration, request: Request) -> dict:
    from .. import accounts

    key = "register:" + client_ip(request)
    blocked, remaining = registration_limiter.is_blocked(key)
    if blocked:
        raise HTTPException(429, "Zu viele Registrierungsversuche. Bitte später erneut versuchen",
                            headers={"Retry-After": str(remaining + 1)})
    registration_limiter.record_fail(key)
    accounts.register(get_db(), payload.username, hash_password(payload.password),
                      invitation_token=payload.invitation_token.strip())
    return {"token": create_session(payload.username, request=request), "token_type": "bearer",
            "expires_in": 60 * 60 * 24 * 14, **_access_payload(payload.username)}


@router.post("/login")
def native_login(payload: NativeLogin, request: Request) -> dict:
    username = payload.username.strip()
    ip = client_ip(request)
    ip_key = f"ip:{ip}"
    limiter_key = login_actor_key(ip, username)
    blocked_ip, remaining_ip = login_ip_limiter.is_blocked(ip_key)
    blocked_user, remaining_user = login_limiter.is_blocked(limiter_key)
    if blocked_ip or blocked_user:
        remaining = max(remaining_ip, remaining_user)
        raise HTTPException(
            429,
            f"Zu viele Login-Versuche. Erneut versuchen in {remaining + 1} Sekunden.",
            headers={"Retry-After": str(remaining + 1)},
        )
    if not check_credentials(username, payload.password):
        login_ip_limiter.record_fail(ip_key)
        login_limiter.record_fail(limiter_key)
        raise HTTPException(401, "Benutzername oder Passwort falsch")
    login_limiter.record_success(limiter_key)
    return {
        "token": create_session(username, request=request),
        "token_type": "bearer",
        "expires_in": 60 * 60 * 24 * 14,
        **_access_payload(username),
    }


@router.get("/session")
def native_session(request: Request) -> dict:
    username = request_user(request)
    if not username:
        raise HTTPException(401, "Authentication required")
    if request_is_guest(request):
        return guest_access_payload()
    return _access_payload(username)


@router.post("/guest")
def guest_login(request: Request) -> dict:
    key = "guest:" + client_ip(request)
    blocked, remaining = guest_limiter.is_blocked(key)
    if blocked:
        raise HTTPException(429, "Zu viele Gastanmeldungen. Bitte später erneut versuchen.",
                            headers={"Retry-After": str(remaining + 1)})
    guest_limiter.record_fail(key)
    return {"token": create_guest_session(), "token_type": "bearer",
            "expires_in": GUEST_MAX_AGE, **guest_access_payload()}


@router.post("/logout")
def native_logout(request: Request) -> dict:
    """Widerruft ausschließlich die aktuelle Sitzung; auch ohne Token idempotent."""
    return {"ok": True, "revoked": revoke_current_session(request)}


@router.post("/logout-all")
def logout_all(request: Request) -> dict:
    """Widerruft alle Sitzungen einschließlich des aktuellen Geräts."""
    if request_is_guest(request):
        return {"ok": True}
    username = request_user(request)
    if not username:
        return {"ok": True}
    revoked = get_db().user_revoke_sessions(username)
    return {"ok": True, "revoked": revoked}
