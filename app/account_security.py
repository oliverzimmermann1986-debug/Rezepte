"""Recent authentication for sensitive self-service account operations."""
import time

from fastapi import HTTPException, Request

from .auth import request_is_guest, request_session, request_user, verify_password
from .db import get_db
from .security import LoginRateLimiter, client_ip

recent_auth_limiter = LoginRateLimiter(max_fails=10, window_sec=600, ban_sec=600)


def require_recent_auth(request: Request, current_password: str = "") -> dict:
    if request_is_guest(request):
        raise HTTPException(403, "Bitte zuerst ein Konto erstellen oder anmelden")
    user = get_db().user_get_by_name(request_user(request) or "")
    if not user or user["disabled"]:
        raise HTTPException(401, "Bitte erneut anmelden")
    key = f"account:{user['id']}:{client_ip(request)}"
    blocked, remaining = recent_auth_limiter.is_blocked(key)
    if blocked:
        raise HTTPException(429, "Zu viele Versuche. Bitte später erneut versuchen",
                            headers={"Retry-After": str(remaining + 1)})
    if current_password:
        if verify_password(current_password, user["password_hash"]):
            recent_auth_limiter.record_success(key)
            return user
    else:
        session = request_session(request)
        if (session and session["auth_method"] in {"apple", "google"}
                and 0 <= time.time() - session["authenticated_at"] <= 300):
            recent_auth_limiter.record_success(key)
            return user
    recent_auth_limiter.record_fail(key)
    if user["password_hash"]:
        raise HTTPException(403, "Bitte das aktuelle Passwort bestätigen oder erneut mit Apple oder Google anmelden")
    raise HTTPException(403, "Bitte erneut mit Apple oder Google anmelden und die Aktion innerhalb von fünf Minuten wiederholen")
