"""Explicit, action-scoped permission for user-initiated external AI processing."""
from contextlib import asynccontextmanager
from contextvars import ContextVar, copy_context
from functools import partial
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

from .config_store import get_config


CONSENT_VERSION = "openai-recipe-v1"
CURRENT_AI_CONSENT: ContextVar[str | None] = ContextVar("ai_processing_consent", default=None)


def require_supported_provider(base_url=None) -> None:
    raw = str(base_url if base_url is not None else get_config().get("ai", "openai", "base_url", default="") or "").strip()
    try:
        target = urlsplit(raw or "https://api.openai.com/v1")
        supported = (target.scheme == "https" and target.hostname == "api.openai.com"
                     and target.port in (None, 443) and target.path.rstrip("/") == "/v1"
                     and target.username is None and target.password is None
                     and not target.query and not target.fragment)
    except ValueError:
        supported = False
    if not supported:
        raise HTTPException(409, detail={
            "code": "AI_PROVIDER_UNSUPPORTED",
            "message": "Dieser Server verwendet einen anderen KI-Dienst. Die Freigabe für OpenAI gilt dafür nicht.",
        })


def validate_ai_consent(value) -> None:
    if not isinstance(value, str) or value != CONSENT_VERSION:
        raise HTTPException(428, detail={
            "code": "AI_CONSENT_REQUIRED",
            "message": "Bitte bestätige vor dieser Aktion die Übermittlung der angezeigten Daten an OpenAI.",
        })
    require_supported_provider()


async def _payload(request: Request):
    content_type = request.headers.get("content-type", "").split(";", 1)[0].lower().strip()
    if content_type == "application/json":
        try:
            value = await request.json()
            return value if isinstance(value, dict) else {}
        except ValueError:
            return {}
    if content_type in {"multipart/form-data", "application/x-www-form-urlencoded"}:
        form = await request.form()
        values = form.getlist("ai_processing_consent")
        return {"ai_processing_consent": values[0] if len(values) == 1 else None}
    return {}


@asynccontextmanager
async def _consent_scope(request: Request):
    payload = request.query_params if request.method == "GET" else await _payload(request)
    if request.method == "GET" and len(request.query_params.getlist("ai_processing_consent")) != 1:
        validate_ai_consent(None)
    validate_ai_consent(payload.get("ai_processing_consent"))
    token = CURRENT_AI_CONSENT.set(CONSENT_VERSION)
    try:
        yield
    finally:
        CURRENT_AI_CONSENT.reset(token)


async def require_ai_consent(request: Request):
    async with _consent_scope(request):
        yield


async def require_audit_ai_consent(request: Request):
    if request.query_params.get("with_ai", "").lower() not in {"1", "true", "t", "on", "yes", "y"}:
        yield
        return
    async with _consent_scope(request):
        yield


async def require_pdf_ai_consent(request: Request):
    payload = await _payload(request)
    # PdfBatchPayload defaults extraction to true; only explicit local work is exempt.
    extract = payload.get("extract_recipe_data", True)
    if extract is False or extract == 0 or (isinstance(extract, str) and extract.lower() in {"false", "f", "0", "off", "no", "n"}):
        yield
        return
    async with _consent_scope(request):
        yield


async def require_pending_save_consent(request: Request):
    payload = await _payload(request)
    if payload.get("action") != "save":
        yield
        return
    async with _consent_scope(request):
        yield


def consent_bound(function):
    """Carry the accepted action and household into one background invocation."""
    return partial(copy_context().run, function)
