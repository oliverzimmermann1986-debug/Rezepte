"""Benutzer-Verwaltung (Multi-User).

Jeder aktive, angemeldete Benutzer besitzt Vollzugriff. Benutzerkonten steuern
nur noch Login, Passwort und Aktivstatus; Rollen werden nicht mehr verwendet.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import hash_password, require_admin
from ..db import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/users", tags=["users"], dependencies=[Depends(require_admin)])

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{3,32}$")
MIN_PW_LEN = 8


def _validate_username(u: str) -> str:
    u = (u or "").strip()
    if not USERNAME_RE.match(u):
        raise HTTPException(
            400, "Username: 3-32 Zeichen, nur a-z A-Z 0-9 _ . -"
        )
    return u


def _validate_password(p: str) -> None:
    if not p or len(p) < MIN_PW_LEN:
        raise HTTPException(400, f"Passwort: mindestens {MIN_PW_LEN} Zeichen")



@router.get("")
def list_users():
    return {"users": get_db().user_list()}


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=MIN_PW_LEN)


@router.post("")
def create_user(payload: UserCreate, current=Depends(require_admin)):
    db = get_db()
    username = _validate_username(payload.username)
    _validate_password(payload.password)
    if db.user_get_by_name(username):
        raise HTTPException(409, f"User '{username}' existiert bereits")

    user_id = db.user_create(username, hash_password(payload.password), role="user")
    logger.info(f"User '{username}' angelegt von '{current.get('username')}'")
    return {"ok": True, "id": user_id, "username": username}


class UserUpdate(BaseModel):
    password: Optional[str] = None
    disabled: Optional[bool] = None


@router.patch("/{user_id}")
def update_user(user_id: int, payload: UserUpdate, current=Depends(require_admin)):
    """Passwort oder Aktivstatus eines Benutzerkontos ändern."""
    db = get_db()
    with db.conn() as c:
        row = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        raise HTTPException(404, "User nicht gefunden")
    target = dict(row)


    if payload.password is not None:
        _validate_password(payload.password)
        db.user_set_password(user_id, hash_password(payload.password))
        logger.info(f"Passwort für '{target['username']}' geändert von '{current.get('username')}'")
    if payload.disabled is True and target.get("username") == current.get("username"):
        raise HTTPException(400, "Du kannst dein eigenes Konto nicht deaktivieren")
    if payload.disabled is not None:
        db.user_set_disabled(user_id, bool(payload.disabled))
        logger.info(
            f"User '{target['username']}' "
            f"{'deaktiviert' if payload.disabled else 'aktiviert'} "
            f"von '{current.get('username')}'"
        )
    return {"ok": True}


@router.delete("/{user_id}")
def delete_user(user_id: int, current=Depends(require_admin)):
    """User löschen. Selbst-Löschung bleibt zum Schutz der Sitzung blockiert."""
    db = get_db()
    target = None
    with db.conn() as c:
        row = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if row:
            target = dict(row)
    if not target:
        raise HTTPException(404, "User nicht gefunden")

    if target.get("username") == current.get("username"):
        raise HTTPException(400, "Du kannst dich nicht selbst löschen")


    db.user_delete(user_id)
    logger.info(f"User '{target['username']}' gelöscht von '{current.get('username')}'")
    return {"ok": True, "deleted": target["username"]}
