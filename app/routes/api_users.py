"""Benutzer-Verwaltung (Multi-User).

Endpoints (admin-only via require_admin):
  GET    /api/users               — Liste aller User (ohne password_hash)
  POST   /api/users               — Neuen User anlegen
  PATCH  /api/users/{user_id}     — Passwort/Role/Disabled ändern
  DELETE /api/users/{user_id}     — User löschen

Lockout-Schutz: vor delete/disable/role=user wird sichergestellt dass
mindestens 1 aktiver Admin übrig bleibt — sonst 400.
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


def _validate_role(r: str) -> str:
    if r not in ("admin", "user"):
        raise HTTPException(400, "role muss 'admin' oder 'user' sein")
    return r


@router.get("")
def list_users():
    return {"users": get_db().user_list()}


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=MIN_PW_LEN)
    role: str = Field("user")


@router.post("")
def create_user(payload: UserCreate, current=Depends(require_admin)):
    db = get_db()
    username = _validate_username(payload.username)
    _validate_password(payload.password)
    role = _validate_role(payload.role)

    if db.user_get_by_name(username):
        raise HTTPException(409, f"User '{username}' existiert bereits")

    user_id = db.user_create(username, hash_password(payload.password), role)
    logger.info(f"User '{username}' (role={role}) angelegt von '{current.get('username')}'")
    return {"ok": True, "id": user_id, "username": username, "role": role}


class UserUpdate(BaseModel):
    password: Optional[str] = None
    role: Optional[str] = None
    disabled: Optional[bool] = None


@router.patch("/{user_id}")
def update_user(user_id: int, payload: UserUpdate, current=Depends(require_admin)):
    """Passwort / Rolle / Disabled-Flag ändern. Lockout-Schutz: wenn der
    letzte aktive Admin auf role=user gesetzt oder disabled wird, → 400."""
    db = get_db()
    with db.conn() as c:
        row = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        raise HTTPException(404, "User nicht gefunden")
    target = dict(row)

    # Lockout-Check vor admin → user oder disabled=true beim letzten Admin
    would_remove_admin = (
        target.get("role") == "admin" and not target.get("disabled") and (
            (payload.role and payload.role != "admin")
            or payload.disabled is True
        )
    )
    if would_remove_admin and db.user_count_active_admins() <= 1:
        raise HTTPException(400, "Letzter aktiver Admin — kann nicht degradiert/deaktiviert werden")

    if payload.password is not None:
        _validate_password(payload.password)
        db.user_set_password(user_id, hash_password(payload.password))
        logger.info(f"Passwort für '{target['username']}' geändert von '{current.get('username')}'")
    if payload.role is not None:
        role = _validate_role(payload.role)
        db.user_set_role(user_id, role)
        logger.info(f"Rolle '{target['username']}' → {role} von '{current.get('username')}'")
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
    """User löschen. Verhindert Lockout (mindestens 1 Admin muss bleiben)
    und Selbst-Löschung (User kann sich nicht eigenes Standbein wegnehmen)."""
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

    if target.get("role") == "admin" and not target.get("disabled"):
        if db.user_count_active_admins() <= 1:
            raise HTTPException(400, "Letzter aktiver Admin — nicht löschbar")

    db.user_delete(user_id)
    logger.info(f"User '{target['username']}' gelöscht von '{current.get('username')}'")
    return {"ok": True, "deleted": target["username"]}
