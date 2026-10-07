"""Browser OIDC and PKCE-bound native sign-in. No provider tokens reach clients."""
from __future__ import annotations

import secrets
from typing import Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from .. import oidc
from ..account_security import require_recent_auth
from ..auth import (SESSION_COOKIE, SESSION_MAX_AGE, create_session, request_is_guest,
                    request_session, request_user, require_auth)
from ..db import get_db
from ..security import LoginRateLimiter, client_ip
from .api_auth import _access_payload

router = APIRouter(tags=["provider-auth"])
flow_limiter = LoginRateLimiter(max_fails=30, window_sec=600, ban_sec=600)
PRIVATE_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def _limit(request: Request, operation: str):
    key = operation + ":" + client_ip(request)
    blocked, remaining = flow_limiter.is_blocked(key)
    if blocked:
        raise HTTPException(429, "Zu viele Anmeldeversuche. Bitte später erneut versuchen",
                            headers={"Retry-After": str(remaining + 1)})
    flow_limiter.record_fail(key)


def _user(request: Request) -> dict:
    if request_is_guest(request):
        raise HTTPException(403, "Bitte zuerst am bestehenden Konto anmelden")
    user = get_db().user_get_by_name(request_user(request) or "")
    if not user or user["disabled"]:
        raise HTTPException(401, "Bitte erneut anmelden")
    return user


class Start(BaseModel):
    platform: Literal["native"] = "native"
    intent: Literal["login", "link"] = "login"
    code_challenge: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    invitation_token: str = Field(default="", max_length=256)
    current_password: str = Field(default="", max_length=512)


class Exchange(BaseModel):
    code: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    code_verifier: str = Field(pattern=r"^[A-Za-z0-9._~-]{43,128}$")


class Confirmation(BaseModel):
    current_password: str = Field(default="", max_length=512)


@router.get("/api/auth/providers")
def providers():
    return JSONResponse({"providers": oidc.available_providers()}, headers=PRIVATE_HEADERS)


def _start(provider, request, *, platform, intent, challenge="", invitation="", binding="", password=""):
    _limit(request, "oidc-start")
    user = _user(request) if intent == "link" else None
    existing = None
    if user:
        with get_db().conn() as c:
            existing = c.execute("SELECT id,subject FROM oidc_identities WHERE user_id=? AND provider=?",
                                 (user["id"], provider)).fetchone()
        if existing is None:
            user = require_recent_auth(request, password)
    return oidc.begin(get_db(), provider, platform=platform, intent=intent, code_challenge=challenge,
                      user=user, session=request_session(request) if user else None,
                      existing_identity=dict(existing) if existing else None,
                      invitation_token=invitation, browser_binding=binding)


@router.post("/api/auth/{provider}/start")
def native_start(provider: str, payload: Start, request: Request):
    result = _start(provider, request, platform="native", intent=payload.intent,
                    challenge=payload.code_challenge, invitation=payload.invitation_token.strip(),
                    password=payload.current_password)
    return JSONResponse(result, headers=PRIVATE_HEADERS)


def _login_payload(identity: dict, request: Request):
    try:
        token = create_session(identity["username"], request=request, auth_method=identity["provider"],
                               expected_identity=identity)
    except ValueError:
        raise HTTPException(401, "Bitte erneut anmelden") from None
    return {"token": token, "token_type": "bearer", "expires_in": SESSION_MAX_AGE,
            **_access_payload(identity["username"])}


@router.post("/api/auth/exchange")
def exchange(payload: Exchange, request: Request):
    _limit(request, "oidc-exchange")
    identity = oidc.exchange(get_db(), payload.code, payload.code_verifier)
    return JSONResponse(_login_payload(identity, request), headers=PRIVATE_HEADERS)


def _cookie_name(state):
    return "__Host-rezepte_oidc_" + oidc._digest(state)[:24]


def _web_start(provider: str, request: Request, intent: str, invitation: str = "", password: str = ""):
    binding = secrets.token_urlsafe(32)
    result = _start(provider, request, platform="web", intent=intent, binding=binding,
                    invitation=invitation[:256].strip(), password=password)
    response = RedirectResponse(result["authorization_url"], status_code=303, headers=PRIVATE_HEADERS)
    # Apple returns via a cross-site form POST; Lax would omit this binding cookie.
    response.set_cookie(_cookie_name(result["flow_id"]), binding, max_age=oidc.FLOW_TTL,
                        secure=True, httponly=True, samesite="none", path="/")
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'; form-action https://appleid.apple.com https://accounts.google.com"
    return response


@router.get("/auth/{provider}/start")
def web_start(provider: str, request: Request, invitation_token: str = ""):
    return _web_start(provider, request, "login", invitation_token)


@router.post("/auth/{provider}/link", dependencies=[Depends(require_auth)])
def web_link(provider: str, request: Request, current_password: str = Form("", max_length=512)):
    return _web_start(provider, request, "link", password=current_password)


def _callback(provider: str, request: Request, values):
    _limit(request, "oidc-callback")
    # Reject parameter pollution rather than letting parser order select credentials.
    for name in ("state", "code", "error"):
        if len(values.getlist(name)) > 1:
            raise HTTPException(400, "Ungültige Anmeldeantwort")
    state = values.get("state", "")
    flow = oidc.consume_flow(get_db(), provider, state, request.cookies.get(_cookie_name(state), ""))
    try:
        code = values.get("code", "")
        if values.get("error") or not isinstance(code, str) or not 1 <= len(code) <= 8192:
            raise HTTPException(400, "Anmeldung abgebrochen. Bitte erneut versuchen")
        result = oidc.finish(get_db(), provider, flow, code)
        if flow["platform"] == "native":
            response = RedirectResponse(oidc.NATIVE_CALLBACK + "?" + urlencode(
                {"code": result["code"], "flow_id": state}), status_code=303, headers=PRIVATE_HEADERS)
        else:
            login = _login_payload(result, request)
            response = RedirectResponse("/account" if flow["intent"] == "link" else "/", status_code=303,
                                        headers=PRIVATE_HEADERS)
            response.set_cookie(SESSION_COOKIE, login["token"], max_age=SESSION_MAX_AGE,
                                secure=True, httponly=True, samesite="lax", path="/")
    except HTTPException:
        if flow["platform"] == "native":
            target = oidc.NATIVE_CALLBACK + "?" + urlencode({"error": "cancelled", "flow_id": state})
        else:
            target = "/login?provider_error=1"
        response = RedirectResponse(target, status_code=303, headers=PRIVATE_HEADERS)
    response.delete_cookie(_cookie_name(state), path="/", secure=True, httponly=True, samesite="none")
    return response


@router.get("/api/auth/{provider}/callback")
def callback_get(provider: str, request: Request):
    return _callback(provider, request, request.query_params)


@router.post("/api/auth/{provider}/callback")
async def callback_post(provider: str, request: Request):
    if provider != "apple":
        raise HTTPException(405, "Ungültige Rückmeldung")
    from starlette.concurrency import run_in_threadpool
    form = await request.form(max_fields=8, max_files=0)
    return await run_in_threadpool(_callback, provider, request, form)


@router.get("/api/account/identities", dependencies=[Depends(require_auth)])
def identities(request: Request):
    user = _user(request)
    return JSONResponse({"identities": oidc.identity_list(get_db(), user["id"]),
                         "providers": oidc.available_providers()}, headers=PRIVATE_HEADERS)


@router.delete("/api/account/identities/{provider}", dependencies=[Depends(require_auth)])
def disconnect(provider: str, payload: Confirmation, request: Request):
    user = require_recent_auth(request, payload.current_password)
    oidc.disconnect(get_db(), user["id"], provider, expected_version=user["session_version"])
    return JSONResponse({"ok": True}, headers=PRIVATE_HEADERS)
