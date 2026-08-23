"""TikTok-/Instagram-Share-Intake.

Ein iOS-Kurzbefehl (Share-Sheet aus TikTok) POSTet eine URL hierher; sie läuft
dann durch die normale Link-Pipeline. Caption und Rezeptdaten werden ohne
Medien-Download ausgewertet; unvollständige Ergebnisse landen in der manuellen
Prüfung.

Auth: KEIN Session-Cookie (ein Kurzbefehl kann keins liefern), stattdessen ein
statisches Token aus der Config (``web.share_token``, lazy generiert). Header
``X-Share-Token`` oder Body-Feld ``token``. Der Endpoint liegt zusätzlich hinter
Cloudflare Access — der Kurzbefehl muss also entweder CF-Service-Token-Header
(CF-Access-Client-Id/Secret) mitschicken, oder es existiert eine CF-Bypass-Policy
für ``/api/share``.

Sicherheit: ``share_token`` ist ein 32-Byte-urlsafe-Secret, Vergleich constant-time.
Bei Leak könnte jemand Link-Junk einreihen — Token geheim halten; Rotation =
``web.share_token`` in der Config leeren + Neustart.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from ..auth import require_auth
from ..config_store import get_config
from ..db import get_db
from ..jobs import scraper as scraper_job
from ..jobs.locks import file_lock_or_none
from ..jobs.task_queue import enqueue

logger = logging.getLogger(__name__)

# Intake — KEINE Session-Auth (Token-geschützt)
router = APIRouter(prefix="/api/share", tags=["share"])
# Info/Token-Anzeige — Session-Auth (zum Einrichten des Kurzbefehls)
info_router = APIRouter(prefix="/api/share", tags=["share"],
                        dependencies=[Depends(require_auth)])


def _share_token() -> str:
    """Liest web.share_token; generiert + persistiert eins, falls fehlend/zu kurz."""
    cfg = get_config()
    tok = cfg.get("web", "share_token", default="") or ""
    if len(tok) < 24:
        tok = secrets.token_urlsafe(32)
        cfg.set("web", "share_token", tok)
        cfg.save()
        logger.warning("web.share_token fehlte/zu kurz — neues generiert.")
    return tok


def _check_token(supplied: Optional[str]) -> None:
    if not supplied or not secrets.compare_digest(str(supplied), _share_token()):
        raise HTTPException(401, "Ungültiges Share-Token")


class ShareIn(BaseModel):
    url: str
    type: str = "recipe"          # recipe | wedding
    token: Optional[str] = None   # Alternative zum X-Share-Token-Header


def _normalized_share_url(value: str) -> str:
    from ..core.email_processor import normalize_content_url

    normalized = normalize_content_url(value)
    if not normalized:
        raise HTTPException(
            400,
            "Share-Import unterstützt nur einzelne TikTok- und Instagram-Posts",
        )
    return normalized


def run_share_ingest_task(payload: dict) -> dict:
    """Queue-Handler: eine URL durch die normale Pipeline verarbeiten."""
    # Auch im Worker erneut validieren: Queue-Inhalte können aus älteren
    # Versionen stammen oder direkt in die Datenbank gelangt sein.
    try:
        url = _normalized_share_url(str(payload.get("url") or ""))
    except HTTPException as exc:
        return {"ok": False, "error": str(exc.detail)}
    content_type = str(payload.get("type") or "recipe")
    existing = get_db().history_get(url)
    if existing and existing.get("target_dir") and Path(existing["target_dir"]).is_dir():
        return {
            "ok": True,
            "url": url,
            "status": "already_processed",
            "target": existing["target_dir"],
        }
    with file_lock_or_none("scraper") as flock:
        if flock is None:
            return {
                "ok": False,
                "retry": True,
                "url": url,
                "error": "Scraper ist momentan belegt",
            }
        result = scraper_job.get_scraper_job().process_url(
            {
                "url": url,
                "type": content_type,
                # Die native Intake-Route legt sofort einen sichtbaren
                # Pending-Platzhalter an. Der Worker muss diesen analysieren,
                # statt ihn als bereits bekannten Import zu überspringen.
                "reanalyze_existing": True,
            }
        )
        logger.info("Share-Intake %s → %s", url, result.get("status"))
        return {"ok": result.get("status") != "error", "url": url, **result}


@router.post("")
def share_intake(payload: ShareIn,
                 x_share_token: Optional[str] = Header(None, alias="X-Share-Token")):
    """Nimmt eine URL entgegen, prüft das Token, reiht sie async ein. Antwortet
    sofort (Kurzbefehl-Timeout) — das Ergebnis erscheint danach in Rezepte/Pending."""
    _check_token(x_share_token or payload.token)
    url = _normalized_share_url(payload.url)
    ctype = payload.type if payload.type in ("recipe", "wedding") else "recipe"
    dedupe_key = hashlib.sha256(
        f"{ctype}\0{url}".encode("utf-8")
    ).hexdigest()
    task_id = enqueue(
        "share_ingest",
        {"url": url, "type": ctype},
        dedupe_key=dedupe_key,
    )
    return {"ok": True, "accepted": True, "task_id": task_id, "queued": url}


@info_router.get("/token")
def share_token_info(request: Request):
    """Aktuelles Share-Token + Intake-URL für die Kurzbefehl-Einrichtung (Session-Auth)."""
    base = str(request.base_url).rstrip("/")
    return {
        "token": _share_token(),
        "post_url": f"{base}/api/share",
        "header": "X-Share-Token",
        "body_example": {"url": "https://www.tiktok.com/@user/video/123", "type": "recipe"},
    }
