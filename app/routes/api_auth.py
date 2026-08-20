"""Native-App-Authentifizierung mit widerrufbaren Bearer-Sitzungen."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import check_credentials, create_session, request_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


class NativeLogin(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=512)


@router.post("/login")
def native_login(payload: NativeLogin) -> dict:
    username = payload.username.strip()
    if not check_credentials(username, payload.password):
        raise HTTPException(401, "Benutzername oder Passwort falsch")
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
