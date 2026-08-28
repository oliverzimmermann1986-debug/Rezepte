"""Native-App-Authentifizierung mit widerrufbaren Bearer-Sitzungen."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import (
    GUEST_USERNAME,
    ROLE_ADMIN,
    ROLE_GUEST,
    ROLE_USER,
    auth_disabled,
    check_credentials,
    create_guest_session,
    create_session,
    request_is_guest,
    request_user,
)
from ..db import get_db
from ..security import client_ip, login_limiter, request_is_from_trusted_proxy

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _access_payload(username: str, *, read_only: bool = False) -> dict:
    """Einheitlicher Rollenvertrag für Login und Sitzungsprüfung.

    Ein noch nicht in die Datenbank migrierter Legacy-Config-Benutzer ist der
    bestehende Betreiber und bleibt deshalb Administrator. Reguläre DB-Konten
    werden ausschließlich anhand ihrer gespeicherten Rolle ausgewertet.
    """
    if read_only:
        role = ROLE_GUEST
    elif auth_disabled():
        role = ROLE_ADMIN
    else:
        user = get_db().user_get_by_name(username)
        role = (user or {}).get("role") or ROLE_ADMIN
        if role not in {ROLE_USER, ROLE_ADMIN}:
            role = ROLE_USER
    is_admin = role == ROLE_ADMIN
    return {
        "username": username,
        "role": role,
        "is_admin": is_admin,
        "full_access": is_admin,
        "read_only": read_only,
    }


class NativeLogin(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=512)


@router.post("/login")
def native_login(payload: NativeLogin, request: Request) -> dict:
    username = payload.username.strip()
    ip = client_ip(request)
    ip_key = f"ip:{ip}"
    limiter_key = f"ip-user:{ip}|{username.casefold()}"
    blocked_ip, remaining_ip = login_limiter.is_blocked(ip_key)
    blocked_user, remaining_user = login_limiter.is_blocked(limiter_key)
    if blocked_ip or blocked_user:
        remaining = max(remaining_ip, remaining_user)
        raise HTTPException(
            429,
            f"Zu viele Login-Versuche. Erneut versuchen in {remaining + 1} Sekunden.",
            headers={"Retry-After": str(remaining + 1)},
        )
    if auth_disabled():
        if not request_is_from_trusted_proxy(request):
            raise HTTPException(403, "Unsichere direkte Verbindung")
        login_limiter.record_success(limiter_key)
        return {
            "token": "cloudflare-access",
            "token_type": "bearer",
            "expires_in": 60 * 60 * 24 * 14,
            **_access_payload("local"),
        }
    if not check_credentials(username, payload.password):
        login_limiter.record_fail(ip_key)
        login_limiter.record_fail(limiter_key)
        raise HTTPException(401, "Benutzername oder Passwort falsch")
    login_limiter.record_success(limiter_key)
    return {
        "token": create_session(username),
        "token_type": "bearer",
        "expires_in": 60 * 60 * 24 * 14,
        **_access_payload(username),
    }


@router.post("/guest")
def native_guest_login(request: Request) -> dict:
    """Gibt einen signierten Gasttoken aus, ohne ein Benutzerkonto anzulegen."""
    if auth_disabled() and not request_is_from_trusted_proxy(request):
        raise HTTPException(403, "Unsichere direkte Verbindung")
    return {
        "token": create_guest_session(),
        "token_type": "bearer",
        "expires_in": 60 * 60 * 24 * 14,
        **_access_payload(GUEST_USERNAME, read_only=True),
    }


@router.get("/session")
def native_session(request: Request) -> dict:
    username = request_user(request)
    if not username:
        raise HTTPException(401, "Authentication required")
    return _access_payload(username, read_only=request_is_guest(request))


@router.post("/logout")
def native_logout(request: Request) -> dict:
    """Widerruft die aktuelle Benutzer-Sitzungsfamilie serverseitig.

    Das System führt bewusst keine einzelne Token-Tabelle. Deshalb werden beim
    Abmelden alle noch offenen Sitzungen dieses Benutzers invalidiert.
    """
    if request_is_guest(request):
        return {"ok": True}
    username = request_user(request)
    if not username:
        return {"ok": True}
    if auth_disabled():
        return {"ok": True}
    revoked = get_db().user_revoke_sessions(username)
    return {"ok": True, "revoked": revoked}
