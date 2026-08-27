"""TikTok-/Instagram-Share-Intake.

Ein iOS-Kurzbefehl (Share-Sheet aus TikTok) POSTet eine URL hierher; sie läuft
dann durch die normale Link-Pipeline. Caption und Rezeptdaten werden ohne
Medien-Download ausgewertet; unvollständige Ergebnisse landen in der manuellen
Prüfung.

Auth: KEIN Session-Cookie (ein Kurzbefehl kann keins liefern), stattdessen ein
individuelles, nur gehasht gespeichertes Intake-Token. Das Klartext-Token wird
nur beim Erstellen ausgegeben. Header ``X-Share-Token`` oder Body-Feld ``token``.
Der Endpoint liegt zusätzlich hinter
Cloudflare Access — der Kurzbefehl muss also entweder CF-Service-Token-Header
(CF-Access-Client-Id/Secret) mitschicken, oder es existiert eine CF-Bypass-Policy
für ``/api/share``.

Sicherheit: Der Intake ist standardmäßig deaktiviert, hat IP-/Token-Limits und
eine begrenzte Queue. Deaktivieren löscht das Token sofort.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import threading
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..auth import require_admin
from ..config_store import get_config
from ..db import get_db
from ..jobs import scraper as scraper_job
from ..jobs.locks import file_lock_or_none
from ..jobs.task_queue import enqueue
from ..security import LoginRateLimiter, client_ip

logger = logging.getLogger(__name__)

# Intake — KEINE Session-Auth (Token-geschützt)
router = APIRouter(prefix="/api/share", tags=["share"])
# Token-Verwaltung — Session-Auth (zum Einrichten des Kurzbefehls)
info_router = APIRouter(prefix="/api/share", tags=["share"],
                        dependencies=[Depends(require_admin)])

_TOKEN_LOCK = threading.Lock()
_SHARE_LIMITER = LoginRateLimiter(
    max_fails=30,
    window_sec=5 * 60,
    ban_sec=15 * 60,
)


def _share_enabled() -> bool:
    cfg = get_config()
    if not bool(cfg.get("web", "share_enabled", default=False)):
        raise HTTPException(503, "Share-Intake ist deaktiviert")
    return True


def _check_token(supplied: Optional[str]) -> str:
    _share_enabled()
    if not supplied or len(str(supplied)) < 32:
        raise HTTPException(401, "Ungültiges Share-Token")
    digest = hashlib.sha256(str(supplied).encode("utf-8")).hexdigest()
    token = get_db().share_intake_token_consume(digest)
    if not token:
        raise HTTPException(401, "Ungültiges Share-Token")
    return str(token["id"])


def _consume_rate_limit(key: str) -> None:
    blocked, remaining = _SHARE_LIMITER.is_blocked(key)
    if blocked:
        raise HTTPException(
            429,
            "Zu viele Share-Importe",
            headers={"Retry-After": str(remaining + 1)},
        )
    # Dieser Limiter zählt absichtlich jeden Intake, nicht nur Auth-Fehler.
    _SHARE_LIMITER.record_fail(key)


class ShareIn(BaseModel):
    url: str
    type: str = "recipe"          # recipe | wedding
    token: Optional[str] = None   # Alternative zum X-Share-Token-Header


class ShareTokenCreate(BaseModel):
    name: str = Field(default="Kurzbefehl", min_length=1, max_length=80)


def _new_token(name: str, created_by: str) -> dict:
    token_id = secrets.token_urlsafe(12)
    token = f"{token_id}.{secrets.token_urlsafe(32)}"
    get_db().share_intake_token_create(
        token_id,
        hashlib.sha256(token.encode("utf-8")).hexdigest(),
        name.strip(),
        created_by,
    )
    return {"id": token_id, "name": name.strip(), "token": token}


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
        logger.info(
            "Share-Intake url_sha256=%s → %s",
            hashlib.sha256(url.encode("utf-8")).hexdigest()[:16],
            result.get("status"),
        )
        return {"ok": result.get("status") != "error", "url": url, **result}


@router.post("")
def share_intake(payload: ShareIn, request: Request,
                 x_share_token: Optional[str] = Header(None, alias="X-Share-Token")):
    """Nimmt eine URL entgegen, prüft das Token, reiht sie async ein. Antwortet
    sofort (Kurzbefehl-Timeout) — das Ergebnis erscheint danach in Rezepte/Pending."""
    supplied = x_share_token or payload.token
    _consume_rate_limit(f"ip:{client_ip(request)}")
    if supplied:
        fingerprint = hashlib.sha256(str(supplied).encode("utf-8")).hexdigest()[:16]
        _consume_rate_limit(f"token:{fingerprint}")
    token_id = _check_token(supplied)
    url = _normalized_share_url(payload.url)
    ctype = payload.type if payload.type in ("recipe", "wedding") else "recipe"
    dedupe_key = hashlib.sha256(
        f"{ctype}\0{url}".encode("utf-8")
    ).hexdigest()
    cfg = get_config()
    try:
        queue_limit = max(1, min(1000, int(
            cfg.get("web", "share_queue_limit", default=100) or 100
        )))
        task_id = enqueue(
            "share_ingest",
            {"url": url, "type": ctype},
            dedupe_key=dedupe_key,
            max_active=queue_limit,
        )
    except OverflowError as exc:
        raise HTTPException(
            429,
            str(exc),
            headers={"Retry-After": "60"},
        ) from exc
    return {
        "ok": True,
        "accepted": True,
        "task_id": task_id,
        "credential_id": token_id,
    }


@info_router.get("/token")
def share_token_info(request: Request, response: Response):
    """Token-Metadaten; Secrets werden nach Erstellung nie erneut angezeigt."""
    base = str(request.base_url).rstrip("/")
    cfg = get_config()
    response.headers["Cache-Control"] = "private, no-store"
    enabled = bool(cfg.get("web", "share_enabled", default=False))
    return {
        "enabled": enabled,
        "token": None,
        "tokens": get_db().share_intake_tokens_list(),
        "post_url": f"{base}/api/share",
        "header": "X-Share-Token",
        "body_example": {"url": "https://www.tiktok.com/@user/video/123", "type": "recipe"},
    }


@info_router.post("/token/rotate")
def rotate_share_token(response: Response) -> dict:
    with _TOKEN_LOCK:
        cfg = get_config()
        get_db().share_intake_tokens_revoke_all()
        cfg.set("web", "share_enabled", True)
        cfg.set("web", "share_token", "")
        cfg.save()
        created = _new_token("Rotiertes Standard-Token", "admin")
    response.headers["Cache-Control"] = "private, no-store"
    return {"ok": True, "enabled": True, **created}


@info_router.post("/tokens")
def create_share_token(payload: ShareTokenCreate, response: Response) -> dict:
    with _TOKEN_LOCK:
        cfg = get_config()
        cfg.set("web", "share_enabled", True)
        cfg.set("web", "share_token", "")
        cfg.save()
        created = _new_token(payload.name, "admin")
    response.headers["Cache-Control"] = "private, no-store"
    return {"ok": True, "enabled": True, **created}


@info_router.delete("/tokens/{token_id}")
def revoke_share_token(token_id: str) -> dict:
    if not get_db().share_intake_token_revoke(token_id):
        raise HTTPException(404, "Share-Token nicht gefunden")
    return {"ok": True, "id": token_id}


@info_router.post("/disable")
def disable_share_intake() -> dict:
    with _TOKEN_LOCK:
        cfg = get_config()
        get_db().share_intake_tokens_revoke_all()
        cfg.set("web", "share_enabled", False)
        cfg.set("web", "share_token", "")
        cfg.save()
    return {"ok": True, "enabled": False}
