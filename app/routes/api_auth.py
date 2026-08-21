"""Native-App-Authentifizierung mit widerrufbaren Bearer-Sitzungen."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import auth_disabled, check_credentials, create_session, request_user
from ..db import get_db
from ..security import client_ip, login_limiter

router = APIRouter(prefix="/api/auth", tags=["auth"])


class NativeLogin(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=512)


@router.post("/login")
def native_login(payload: NativeLogin, request: Request) -> dict:
    username = payload.username.strip()
    ip = client_ip(request)
    limiter_key = f"{ip}|{username.casefold()}"
    blocked_ip, remaining_ip = login_limiter.is_blocked(ip)
    blocked_user, remaining_user = login_limiter.is_blocked(limiter_key)
    if blocked_ip or blocked_user:
        remaining = max(remaining_ip, remaining_user)
        raise HTTPException(
            429,
            f"Zu viele Login-Versuche. Erneut versuchen in {remaining + 1} Sekunden.",
            headers={"Retry-After": str(remaining + 1)},
        )
    if auth_disabled():
        login_limiter.record_success(ip)
        login_limiter.record_success(limiter_key)
        return {
            "token": "cloudflare-access",
            "token_type": "bearer",
            "expires_in": 60 * 60 * 24 * 14,
            "username": "local",
        }
    if not check_credentials(username, payload.password):
        login_limiter.record_fail(ip)
        login_limiter.record_fail(limiter_key)
        raise HTTPException(401, "Benutzername oder Passwort falsch")
    login_limiter.record_success(ip)
    login_limiter.record_success(limiter_key)
    return {
        "token": create_session(username),
        "token_type": "bearer",
        "expires_in": 60 * 60 * 24 * 14,
        "username": username,
    }


@router.get("/session")
def native_session(request: Request) -> dict:
    username = request_user(request)
    if not username:
        raise HTTPException(401, "Authentication required")
    return {"username": username, "full_access": True}


@router.post("/logout")
def native_logout(request: Request) -> dict:
    """Widerruft die aktuelle Benutzer-Sitzungsfamilie serverseitig.

    Das System führt bewusst keine einzelne Token-Tabelle. Deshalb werden beim
    Abmelden alle noch offenen Sitzungen dieses Benutzers invalidiert.
    """
    username = request_user(request)
    if not username:
        return {"ok": True}
    if auth_disabled():
        return {"ok": True}
    revoked = get_db().user_revoke_sessions(username)
    return {"ok": True, "revoked": revoked}
