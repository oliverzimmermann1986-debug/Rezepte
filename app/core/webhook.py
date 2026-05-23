"""Webhook-Notifier (Telegram-Ersatz).

Generischer HTTPS-POST mit JSON-Payload. User trägt eine oder mehrere
Ziel-URLs ein und wählt für jede die Events. Funktioniert mit:
  - Discord-Webhooks (https://discord.com/api/webhooks/...)
  - ntfy.sh / ntfy-self-hosted
  - Slack-Incoming-Webhooks
  - Microsoft Teams
  - Eigene Endpoints (Home Assistant, Node-RED, n8n, ...)

Format:
  {
    "event": "scraper_done" | "backup_done" | "job_failed" | "pending_high",
    "timestamp": "2026-05-22T15:30:00+00:00",
    "host": "scrapper",
    "summary": { ... event-spezifisch ... }
  }

Fire-and-forget über einen Modul-globalen ThreadPool, wie der frühere
Telegram-Notifier. Wenn ein Webhook tot ist blockiert er nicht den Job.
"""
from __future__ import annotations

import atexit
import json
import logging
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Dict, List

import requests

logger = logging.getLogger(__name__)


# Globaler Pool. 2 Worker sind genug - Webhooks gehen schnell oder fail-fast.
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="webhook")


def _shutdown_pool() -> None:
    _POOL.shutdown(wait=False, cancel_futures=True)


atexit.register(_shutdown_pool)


# Discord erkennt manche Felder spezifisch. Wir bauen für Discord ein
# 'embeds'-Format zusätzlich zum 'content', damit's gut aussieht.
def _format_discord(payload: dict) -> dict:
    color_by_event = {
        "scraper_done": 0x22c55e,
        "backup_done": 0x06b6d4,
        "job_failed": 0xef4444,
        "pending_high": 0xeab308,
    }
    color = color_by_event.get(payload["event"], 0x94a3b8)
    summary = payload.get("summary", {})
    fields = []
    for k, v in list(summary.items())[:8]:
        # Discord max 1024 chars pro field value
        sv = str(v)[:1024]
        fields.append({"name": str(k)[:256], "value": sv, "inline": True})
    return {
        "embeds": [{
            "title": payload["event"],
            "timestamp": payload["timestamp"],
            "color": color,
            "fields": fields,
            "footer": {"text": payload.get("host", "")},
        }]
    }


def _detect_format(url: str, payload: dict) -> dict:
    """Discord-URLs kriegen embeds, alles andere kriegt das rohe JSON."""
    if "discord.com/api/webhooks/" in url:
        return _format_discord(payload)
    return payload


def _post_one(target: dict, payload: dict) -> bool:
    """Sendet an genau einen Webhook. Returnt True bei 2xx."""
    url = target.get("url", "").strip()
    if not url:
        return False
    name = target.get("name", url[:40])
    try:
        body = _detect_format(url, payload)
        r = requests.post(url, json=body, timeout=10)
        if 200 <= r.status_code < 300:
            logger.info(f"webhook[{name}] {r.status_code} ok")
            return True
        logger.warning(f"webhook[{name}] HTTP {r.status_code}: {r.text[:200]}")
        return False
    except Exception as e:
        logger.error(f"webhook[{name}] failed: {e}")
        return False


def notify(event: str, summary: dict, *, sync: bool = False) -> List[dict]:
    """Sendet ein Event an alle konfigurierten Webhooks, die das Event abonniert haben.

    Args:
        event:    'scraper_done' | 'backup_done' | 'job_failed' | 'pending_high'
        summary:  Beliebiges JSON-serialisierbares Dict
        sync:     Wenn True wird synchron gesendet (für Tests). Default async.

    Returns:
        Liste der Webhook-Konfigs, an die gesendet wurde (vor dem Send).
    """
    # Spätes Import damit kein Zirkelbezug
    from ..config_store import get_config
    cfg = get_config()
    hooks_cfg = cfg.get("webhooks", default=[]) or []
    payload = {
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "host": socket.gethostname(),
        "summary": summary,
    }
    sent_to = []
    for hook in hooks_cfg:
        if not hook.get("enabled", True):
            continue
        events = hook.get("events") or ["scraper_done", "backup_done", "job_failed"]
        if event not in events:
            continue
        sent_to.append(hook)
        if sync:
            _post_one(hook, payload)
        else:
            try:
                _POOL.submit(_post_one, hook, payload)
            except RuntimeError:
                # Pool down (atexit) - fallback sync
                _post_one(hook, payload)
    return sent_to


def test_webhook(target: dict) -> Dict:
    """Synchroner Test-Send. Wird vom Frontend-Button gerufen."""
    payload = {
        "event": "test",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "host": socket.gethostname(),
        "summary": {"message": "Test-Nachricht vom Scrapper Web-UI"},
    }
    ok = _post_one(target, payload)
    if ok:
        return {"ok": True, "message": f"Webhook {target.get('name', '?')}: 2xx erhalten"}
    return {"ok": False, "error": "Webhook hat nicht mit 2xx geantwortet - siehe Logs"}
