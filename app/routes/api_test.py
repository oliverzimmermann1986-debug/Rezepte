"""API für Verbindungstests aller externen Services."""
from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import require_auth
from ..config_store import get_config
from ..core.email_processor import MailAccount

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/test", tags=["test"], dependencies=[Depends(require_auth)])


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


@router.post("/openai")
def test_openai(req: OpenAITestRequest = None) -> Dict[str, Any]:
    """OpenAI API-Key gültig? GET /v1/models pingen + Model verfügbar.

    Body-Param erlaubt das Testen ohne vorher zu speichern - das Frontend
    schickt die aktuell eingetippten Werte mit. Fallback: aus Config lesen
    wenn nichts mitgeschickt wurde.
    """
    cfg = get_config().get("ai", "openai", default={}) or {}
    api_key = ""
    model = ""
    base_url = ""

    if req:
        api_key = (req.api_key or "").strip()
        model = (req.model or "").strip()
        base_url = (req.base_url or "").strip()

    # Aus Config nachladen falls Body leer (oder noch die Mask-Konstante).
    # Die UI bekommt den gespeicherten Key beim Page-Load als "********" zurück
    # (siehe MASKED in api_config.py). Wenn der User dann nichts ändert und
    # auf "Testen" klickt, käme die Maske hier an - die wollen wir nicht 1:1
    # an OpenAI schicken (sonst 401). Erkennen und durch echten Wert ersetzen.
    if (not api_key
            or api_key == "********"        # Mask-Konstante aus api_config.py
            or api_key.startswith("•")      # Frontend zeigt evtl. Bullets
            or set(api_key) <= {"*", "•"}): # nur Maskenzeichen
        api_key = (cfg.get("api_key") or "").strip()
    if not model:
        model = (cfg.get("model") or "gpt-4o-mini").strip()
    if not base_url:
        base_url = (cfg.get("base_url") or "https://api.openai.com/v1").rstrip("/")
    else:
        base_url = base_url.rstrip("/")

    if not api_key or api_key == "********" or set(api_key) <= {"*", "•"}:
        return {"ok": False, "error": "Kein API-Key - eintragen oder vorher speichern"}

    import requests
    try:
        r = requests.get(
            f"{base_url}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        if r.status_code == 401:
            return {"ok": False, "error": "API-Key ungültig (HTTP 401)"}
        if r.status_code == 403:
            return {"ok": False, "error": "Zugriff verweigert (HTTP 403) - Account-Status prüfen"}
        r.raise_for_status()
        models = [m.get("id", "") for m in r.json().get("data", [])]
    except requests.exceptions.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.response.status_code if e.response else '?'}: {str(e)[:200]}"}
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
# /rclone-Test wurde entfernt: rclone-Sync läuft im separaten Container.


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
