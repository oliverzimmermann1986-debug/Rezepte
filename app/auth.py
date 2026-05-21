"""
Simple Session-Auth.
Single-User-System: Username/Password aus Config, Session-Cookie signiert.
"""
from __future__ import annotations

from fastapi import Request, HTTPException, status
from fastapi.responses import RedirectResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from .config_store import get_config

SESSION_COOKIE = "scrapper_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 14  # 14 Tage


def _serializer() -> URLSafeTimedSerializer:
    secret = get_config().get("web", "secret_key", default="please-change-me")
    return URLSafeTimedSerializer(secret, salt="scrapper-auth")


def check_credentials(username: str, password: str) -> bool:
    cfg_u = get_config().get("web", "username", default="admin")
    cfg_p = get_config().get("web", "password", default="changeme")
    return username == cfg_u and password == cfg_p


def create_session(username: str) -> str:
    return _serializer().dumps({"user": username})


def verify_session(token: str) -> bool:
    try:
        _serializer().loads(token, max_age=SESSION_MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False


async def require_auth(request: Request) -> None:
    # API: 401 JSON
    is_api = request.url.path.startswith("/api/")
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token or not verify_session(token):
        if is_api:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            )
        # Redirect zur Login-Seite
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": f"/login?next={request.url.path}"},
        )
