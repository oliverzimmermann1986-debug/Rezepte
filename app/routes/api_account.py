"""Eigenes Konto und die Einladung einer zweiten Person, ohne Adminrechte."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from .. import accounts
from ..auth import (hash_password, request_is_guest, request_is_read_only, request_session, request_user,
                    require_auth, require_import, validate_new_password)
from ..account_security import require_recent_auth
from ..db import LastActiveAdminError, get_db

router = APIRouter(prefix="/api/account", tags=["account"], dependencies=[Depends(require_auth)])


def _user(request: Request):
    if request_is_guest(request):
        raise HTTPException(403, "Bitte zuerst ein Konto erstellen oder anmelden")
    user = get_db().user_get_by_name(request_user(request) or "")
    if not user or user["disabled"]:
        raise HTTPException(401, "Bitte erneut anmelden")
    return user


@router.get("")
def own_account(request: Request):
    if request_is_read_only(request):
        return {"is_guest": request_is_guest(request), "read_only": True,
                "username": request_user(request), "is_owner": False, "members": [], "invitations": [],
                "max_members": accounts.MAX_ACCOUNT_MEMBERS, "data_scope": "global_read_only", "global_recipes": True}
    user = _user(request)
    return {"is_guest": False, "username": user["username"], **accounts.view(get_db(), user["id"])}


@router.get("/profile")
def own_profile(request: Request):
    user = _user(request)
    return {key: user[key] for key in ("id", "username", "role", "created_at", "last_login_at")} | {
        "password_enabled": bool(user["password_hash"]),
    }


class AccountConfirmation(BaseModel):
    current_password: str = Field(default="", max_length=512)


class PasswordChange(AccountConfirmation):
    new_password: str = Field(min_length=10, max_length=72)

    @field_validator("new_password")
    @classmethod
    def validate_password(cls, value):
        return validate_new_password(value)


@router.post("/password")
def change_password(payload: PasswordChange, request: Request):
    user = require_recent_auth(request, payload.current_password)
    get_db().user_set_password(user["id"], hash_password(payload.new_password), expected_version=user["session_version"])
    return {"ok": True, "reauthenticate": True}


@router.delete("/profile")
def delete_own_profile(payload: AccountConfirmation, request: Request):
    user = require_recent_auth(request, payload.current_password)
    try:
        deleted = get_db().user_delete(user["id"], expected_version=user["session_version"])
    except LastActiveAdminError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not deleted:
        raise HTTPException(404, "Benutzerkonto nicht gefunden")
    return {"ok": True}


@router.get("/sessions")
def own_sessions(request: Request):
    user = _user(request)
    current = request_session(request)
    current_id = current["id"] if current else None
    return {"sessions": [{**session, "is_current": session["id"] == current_id}
                         for session in get_db().session_list(user["id"])]}


@router.delete("/sessions/{session_id}")
def revoke_session(session_id: str, request: Request):
    user = _user(request)
    if not get_db().session_revoke(user["id"], session_id):
        raise HTTPException(404, "Sitzung nicht gefunden")
    return {"ok": True}


@router.post("/invitations", status_code=201)
def create_invitation(request: Request):
    return {"ok": True, **accounts.invite(get_db(), _user(request)["id"])}


@router.delete("/invitations/{invitation_id}")
def revoke_invitation(invitation_id: int, request: Request):
    accounts.revoke(get_db(), _user(request)["id"], invitation_id)
    return {"ok": True}


class InvitationAccept(BaseModel):
    token: str = Field(min_length=16, max_length=256)


@router.post("/invitations/accept")
def accept_invitation(payload: InvitationAccept, request: Request):
    accounts.accept(get_db(), _user(request)["id"], payload.token.strip())
    return {"ok": True}


@router.get("/imports", dependencies=[Depends(require_import)])
def own_imports(request: Request):
    _user(request)
    db = get_db()
    items = []
    for item in db.pending_list(status="pending"):
        if item.get("owner_account_id") != db.account_id:
            continue
        suggestion = dict(item.get("ai_suggestion") or {})
        with db.conn() as c:
            task = c.execute("SELECT status,error FROM background_tasks WHERE kind='share_ingest' AND "
                             "json_extract(payload_json,'$.account_id')=? AND json_extract(payload_json,'$.url')=? ORDER BY id DESC LIMIT 1",
                             (db.account_id, item["url"])).fetchone()
        if task:
            suggestion["analysis_state"] = task["status"] if task["status"] in {"queued", "running", "error"} else "ready"
            if task["error"]:
                suggestion["analysis_error"] = task["error"]
        items.append({"url": item["url"], "status": item["status"], "created_at": item["created_at"],
                      "name": suggestion.get("name"), "suggestion": suggestion,
                      "has_file": bool(item.get("video_path") or item.get("frame_path"))})
    return {"items": items}
