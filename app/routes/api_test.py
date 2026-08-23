"""API für Verbindungstests aller externen Services."""
from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlsplit, urlunsplit

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import require_admin
from ..config_store import get_config
from ..core.email_processor import MailAccount
from ..core.webhook import pinned_https_request, server_configured_request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/test", tags=["test"], dependencies=[Depends(require_admin)])


class MailTestRequest(BaseModel):
    account: str  # 'recipe' | 'wedding'


@router.post("/mail")
def test_mail(req: MailTestRequest) -> Dict[str, Any]:
    """IMAP-Verbindung testen + Anzahl URLs im Postfach zählen."""
    if req.account not in ("recipe", "wedding"):
        raise HTTPException(400, "account muss 'recipe' oder 'wedding' sein")

    cfg = get_config().get("mail", req.account, default={}) or {}
    if not cfg.get("username") or not cfg.get("password"):
        return {"ok": False, "error": "Benutzer/Passwort fehlt"}

    start = time.time()
    try:
        acc = MailAccount(req.account, cfg, req.account)
        urls = acc.fetch_urls()
        elapsed = round(time.time() - start, 2)
        return {
            "ok": True,
            "message": f"IMAP-Verbindung OK ({elapsed}s) – {len(urls)} URLs im Postfach gefunden.",
            "url_count": len(urls),
            "host": cfg.get("imap_host"),
            "elapsed": elapsed,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


class OpenAITestRequest(BaseModel):
    api_key: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None


def _is_masked_secret(value: str) -> bool:
    value = (value or "").strip()
    return bool(value) and (
        value == "********"
        or value.startswith("•")
        or set(value) <= {"*", "•"}
    )


def _normalized_base_url(value: str) -> str:
    value = (value or "").strip().rstrip("/")
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.hostname:
        raise HTTPException(400, "Ungültige OpenAI Base-URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HTTPException(400, "Base-URL darf keine Credentials, Query oder Fragment enthalten")
    host = parsed.hostname.lower()
    try:
        parsed_port = parsed.port
    except ValueError as exc:
        raise HTTPException(400, "Ungültiger Port in der OpenAI Base-URL") from exc
    host_for_netloc = f"[{host}]" if ":" in host else host
    port = f":{parsed_port}" if parsed_port else ""
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), f"{host_for_netloc}{port}", path, "", ""))


@router.post("/openai")
def test_openai(req: OpenAITestRequest = None) -> Dict[str, Any]:
    """OpenAI API-Key gültig? GET /v1/models pingen + Model verfügbar.

    Body-Param erlaubt das Testen ohne vorher zu speichern - das Frontend
    schickt die aktuell eingetippten Werte mit. Fallback: aus Config lesen
    wenn nichts mitgeschickt wurde.
    """
    cfg = get_config().get("ai", "openai", default={}) or {}
    requested_api_key = ""
    model = ""
    requested_base_url = ""

    if req:
        requested_api_key = (req.api_key or "").strip()
        model = (req.model or "").strip()
        requested_base_url = (req.base_url or "").strip()

    configured_key = (cfg.get("api_key") or "").strip()
    configured_base = _normalized_base_url(
        cfg.get("base_url") or "https://api.openai.com/v1"
    )
    base_url = (
        _normalized_base_url(requested_base_url)
        if requested_base_url
        else configured_base
    )
    custom_base = base_url != configured_base

    # Aus Config nur nachladen, wenn die Anfrage exakt die konfigurierte
    # Base-URL verwendet. Sonst könnte ein angemeldeter Benutzer den geheimen
    # Key über Authorization an einen eigenen Host senden.
    # Die UI bekommt den gespeicherten Key beim Page-Load als "********" zurück
    # (siehe MASKED in api_config.py). Bei einer abweichenden URL ist deshalb
    # ein expliziter, unmaskierter Request-Key zwingend.
    explicit_key = requested_api_key and not _is_masked_secret(requested_api_key)
    if custom_base and not explicit_key:
        raise HTTPException(
            400,
            "Für eine abweichende Base-URL muss der API-Key neu eingegeben werden",
        )
    api_key = requested_api_key if explicit_key else configured_key
    if not model:
        model = (cfg.get("model") or "gpt-4o-mini").strip()

    if not api_key or _is_masked_secret(api_key):
        return {"ok": False, "error": "Kein API-Key - eintragen oder vorher speichern"}
    models_url = f"{base_url}/models"
    request_headers = {"Authorization": f"Bearer {api_key}"}
    try:
        if custom_base:
            try:
                r = pinned_https_request(
                    "GET",
                    models_url,
                    headers=request_headers,
                    timeout=10,
                )
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        else:
            r = server_configured_request(
                "GET",
                models_url,
                trusted_private_bases=(configured_base,),
                headers=request_headers,
                timeout=10,
            )
        if 300 <= r.status_code < 400:
            return {"ok": False, "error": "Redirects der Base-URL werden nicht verfolgt"}
        if r.status_code == 401:
            return {"ok": False, "error": "API-Key ungültig (HTTP 401)"}
        if r.status_code == 403:
            return {"ok": False, "error": "Zugriff verweigert (HTTP 403) - Account-Status prüfen"}
        r.raise_for_status()
        models = [m.get("id", "") for m in r.json().get("data", [])]
    except requests.exceptions.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.response.status_code if e.response else '?'}: {str(e)[:200]}"}
    except HTTPException:
        raise
    except Exception as e:
        return {"ok": False, "error": f"Nicht erreichbar: {e}"}

    found = model in models
    if not found:
        # Bei Aliases wie 'gpt-4o-mini' können Vollnamen 'gpt-4o-mini-2024-07-18' sein
        partial = [m for m in models if model in m]
        if partial:
            return {"ok": True, "message": f"Modell-Variante '{partial[0]}' ✓",
                    "model_count": len(models)}
        return {"ok": False, "error": f"Modell '{model}' nicht verfügbar (für deinen Account)"}
    return {"ok": True, "message": f"Modell '{model}' ✓", "model_count": len(models)}


# /telegram-Test wurde entfernt: Telegram-Benachrichtigungen sind raus.


@router.post("/paths")
def test_paths() -> Dict[str, Any]:
    """Prüft ob alle konfigurierten Pfade existieren und beschreibbar sind."""
    cfg = get_config().get("paths", default={}) or {}
    results = {}
    all_ok = True
    for key, p in cfg.items():
        path = Path(p) if p else None
        if not path:
            results[key] = {"path": p, "ok": False, "error": "leer"}
            all_ok = False
            continue
        exists = path.exists()
        writable = os.access(p, os.W_OK) if exists else False
        results[key] = {
            "path": p,
            "exists": exists,
            "writable": writable,
            "ok": exists and writable,
        }
        if not (exists and writable):
            all_ok = False
    return {"ok": all_ok, "paths": results}


@router.post("/ytdlp")
def test_ytdlp() -> Dict[str, Any]:
    """yt-dlp Binary vorhanden und Version?"""
    binary = get_config().get("ytdlp", "binary", default="/opt/scrapper/venv/bin/yt-dlp")
    try:
        r = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return {"ok": False, "error": r.stderr.strip() or "exit != 0"}
        return {"ok": True, "version": r.stdout.strip(), "binary": binary}
    except FileNotFoundError:
        return {"ok": False, "error": f"Binary nicht gefunden: {binary}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


class WebhookTestRequest(BaseModel):
    name: str = "test"
    url: str


@router.post("/webhook")
def test_webhook(req: WebhookTestRequest) -> Dict[str, Any]:
    """Sendet eine Test-Nachricht an einen Webhook."""
    from ..core.webhook import test_webhook as do_test
    return do_test({"name": req.name, "url": req.url, "enabled": True})
