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
from ..core.analyzer import OllamaAnalyzer
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


@router.post("/ollama")
def test_ollama() -> Dict[str, Any]:
    """Ollama-Server erreichbar? Beide Modelle (fast + fallback) verfügbar?"""
    cfg = get_config().get("ai", "ollama", default={}) or {}
    if not cfg.get("enabled"):
        return {"ok": False, "error": "Ollama ist in der Config deaktiviert"}
    url = cfg.get("url", "")
    fast_model = cfg.get("model", "")
    fallback = (cfg.get("fallback_model") or "").strip()
    if not url or not fast_model:
        return {"ok": False, "error": "URL oder Modell fehlt"}

    # Modelle auflisten
    try:
        import requests
        r = requests.get(f"{url.rstrip('/')}/api/tags", timeout=10)
        r.raise_for_status()
        installed = [m.get("name", "") for m in r.json().get("models", [])]
    except Exception as e:
        return {"ok": False, "error": f"Server nicht erreichbar: {e}"}

    found_fast = any(fast_model in m for m in installed)
    found_fb   = bool(fallback) and any(fallback in m for m in installed)

    if not found_fast:
        return {"ok": False, "error": f"Modell '{fast_model}' nicht installiert. Verfügbar: {', '.join(installed)}"}

    msg_parts = [f"Fast-Modell '{fast_model}' ✓"]
    if fallback:
        if found_fb:
            msg_parts.append(f"Fallback '{fallback}' ✓")
        else:
            return {"ok": False, "error": f"Fallback '{fallback}' nicht installiert. Verfügbar: {', '.join(installed)}"}
    else:
        msg_parts.append("kein Fallback konfiguriert")

    return {"ok": True, "message": " · ".join(msg_parts), "installed": installed}


@router.post("/openai")
def test_openai() -> Dict[str, Any]:
    """OpenAI API-Key gültig? GET /v1/models pingen + Model verfügbar."""
    cfg = get_config().get("ai", "openai", default={}) or {}
    api_key = (cfg.get("api_key") or "").strip()
    model = (cfg.get("model") or "gpt-4o-mini").strip()
    base_url = (cfg.get("base_url") or "https://api.openai.com/v1").rstrip("/")
    if not api_key or api_key.startswith("•"):
        return {"ok": False, "error": "Kein API-Key konfiguriert"}

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


class RcloneTestRequest(BaseModel):
    pair_index: Optional[int] = None  # wenn None: nur listremotes


@router.post("/rclone")
def test_rclone(req: RcloneTestRequest) -> Dict[str, Any]:
    """rclone-Konfiguration und Remote-Zugriff testen."""
    try:
        # 1. listremotes
        r = subprocess.run(
            ["rclone", "listremotes"], capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            return {"ok": False, "error": f"rclone listremotes: {r.stderr.strip()}"}
        remotes = [ln.strip().rstrip(":") for ln in r.stdout.splitlines() if ln.strip()]
        if not remotes:
            return {"ok": False, "error": "Keine rclone-Remotes konfiguriert. `rclone config` ausführen."}

        backup = get_config().get("backup", default={}) or {}
        configured_remote = backup.get("rclone_remote", "")
        result = {
            "ok": True,
            "remotes": remotes,
            "configured_remote": configured_remote,
            "remote_exists": configured_remote in remotes,
        }

        if not result["remote_exists"]:
            result["ok"] = False
            result["error"] = (
                f"Konfigurierter Remote '{configured_remote}' nicht in rclone gefunden. "
                f"Verfügbar: {', '.join(remotes)}"
            )
            return result

        # 2. Optional: konkretes Paar testen (lsd auf den remote-Pfad)
        if req.pair_index is not None:
            pairs = backup.get("pairs") or []
            if req.pair_index >= len(pairs):
                result["error"] = "pair_index außerhalb der Liste"
                result["ok"] = False
                return result
            pair = pairs[req.pair_index]
            remote_path = pair.get("remote", "")
            local_path = pair.get("local", "")

            # remote-Test
            r2 = subprocess.run(
                ["rclone", "size", remote_path], capture_output=True, text=True, timeout=60,
            )
            result["remote_path"] = remote_path
            result["remote_size_output"] = r2.stdout.strip()[:300] if r2.returncode == 0 else r2.stderr.strip()[:300]

            # lokaler Pfad
            result["local_path"] = local_path
            result["local_exists"] = Path(local_path).exists()
            if not result["local_exists"]:
                result["ok"] = False
                result["error"] = f"Lokaler Pfad existiert nicht: {local_path}"

        result["message"] = (
            f"rclone OK – {len(remotes)} Remote(s): {', '.join(remotes)}"
        )
        return result
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "rclone Timeout"}
    except FileNotFoundError:
        return {"ok": False, "error": "rclone Binary nicht gefunden"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


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
