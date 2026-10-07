"""Eigenes Konto und die Einladung einer zweiten Person, ohne Adminrechte."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import accounts
from ..auth import request_is_guest, request_user, require_auth
from ..db import get_db

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
    if request_is_guest(request):
        return {"is_guest": True, "is_owner": False, "members": [], "invitations": [],
                "max_members": accounts.MAX_ACCOUNT_MEMBERS, "data_scope": "global_read_only", "global_recipes": True}
    user = _user(request)
    return {"is_guest": False, "username": user["username"], **accounts.view(get_db(), user["id"])}


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


@router.get("/imports")
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
                      "name": suggestion.get("name"), "suggestion": suggestion})
    return {"items": items}
