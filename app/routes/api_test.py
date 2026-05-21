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
from ..core.notifier import TelegramNotifier

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
    """Ollama-Server erreichbar? Modell verfügbar?"""
    cfg = get_config().get("ai", "ollama", default={}) or {}
    if not cfg.get("enabled"):
        return {"ok": False, "error": "Ollama ist in der Config deaktiviert"}
    url = cfg.get("url", "")
    model = cfg.get("model", "")
    if not url or not model:
        return {"ok": False, "error": "URL oder Modell fehlt"}

    try:
        analyzer = OllamaAnalyzer(url, model, timeout=10)
        ok = analyzer.health()
        if ok:
            return {"ok": True, "message": f"Ollama erreichbar, Modell '{model}' verfügbar."}
        return {"ok": False, "error": f"Modell '{model}' nicht installiert auf {url}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@router.post("/openai")
def test_openai() -> Dict[str, Any]:
    """OpenAI API-Key gültig?"""
    cfg = get_config().get("ai", "openai", default={}) or {}
    key = cfg.get("api_key", "")
    if not cfg.get("enabled"):
        return {"ok": False, "error": "OpenAI ist in der Config deaktiviert"}
    if not key or key.startswith("sk-..."):
        return {"ok": False, "error": "API-Key nicht gesetzt"}
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key)
        # Sehr kleiner Request - listet verfügbare Modelle
        models = client.models.list()
        names = [m.id for m in list(models)[:10]]
        return {
            "ok": True,
            "message": f"OpenAI OK – {len(names)} Modelle abgerufen",
            "models_sample": names[:5],
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


class TelegramTestRequest(BaseModel):
    bot: str = "recipe"  # 'recipe' | 'wedding' | 'backup'


@router.post("/telegram")
def test_telegram(req: TelegramTestRequest) -> Dict[str, Any]:
    """Telegram Test-Nachricht versenden."""
    tg = get_config().get("telegram", default={}) or {}
    if not tg.get("enabled", True):
        return {"ok": False, "error": "Telegram ist in der Config deaktiviert"}

    if req.bot == "recipe":
        token, chat = tg.get("recipe_bot_token", ""), tg.get("recipe_chat_id", "")
        label = "Rezept-Bot"
    elif req.bot == "wedding":
        token = tg.get("wedding_bot_token", "") or tg.get("recipe_bot_token", "")
        chat = tg.get("wedding_chat_id", "") or tg.get("recipe_chat_id", "")
        label = "Hochzeit-Bot"
    elif req.bot == "backup":
        token = tg.get("backup_bot_token", "") or tg.get("recipe_bot_token", "")
        chat = tg.get("backup_chat_id", "") or tg.get("recipe_chat_id", "")
        label = "Backup-Bot"
    else:
        raise HTTPException(400, "bot muss recipe/wedding/backup sein")

    notifier = TelegramNotifier(token, chat, label=req.bot)
    if not notifier.enabled:
        return {"ok": False, "error": f"Token oder Chat-ID fehlt für {label}"}

    from datetime import datetime
    msg_id = notifier.send(
        f"✅ <b>{label} Test</b>\n"
        f"Verbindung funktioniert.\n"
        f"<i>{datetime.now():%Y-%m-%d %H:%M:%S}</i>"
    )
    if msg_id:
        return {"ok": True, "message": f"Test-Nachricht an {label} gesendet (msg_id={msg_id})"}
    return {"ok": False, "error": "Senden fehlgeschlagen – Token oder Chat-ID falsch?"}


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
