"""Geschützter Reverse-Proxy zur bestehenden Einkaufsliste.

Die vollwertige Einkaufsliste bleibt ein eigener Dienst und behält ihre eigene
Datenbank. Das Rezepte-Frontend spricht nur diesen Proxy an; App- und
Cloudflare-Tokens bleiben dadurch ausschließlich auf dem Server.
"""
from __future__ import annotations

import logging
import posixpath
from typing import Any
from urllib.parse import unquote

import requests
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from ..auth import require_auth
from ..config_store import get_config

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/einkauf",
    tags=["einkauf"],
    dependencies=[Depends(require_auth)],
)

# Nur die für die gemeinsame Oberfläche benötigten Bereiche freigeben.
# Administrative Sicherungs-, Restore- und Audit-Endpunkte bleiben bewusst
# ausschließlich im Einkauf-Dienst erreichbar.
_ALLOWED = {
    "state",
    "items",
    "consolidate",
    "list",
    "stats",
    "stamm",
    "suggest",
    "recurring",
    "templates",
}


def _validated_proxy_path(path: str) -> str:
    """Normalisiert vor der Allowlist-Prüfung und blockiert Traversal.

    ``requests`` normalisiert ``..`` beim Senden. Eine Prüfung nur des ersten
    Rohsegments würde deshalb z.B. ``items/../admin`` als ``items`` freigeben,
    am Zieldienst aber ``/admin`` aufrufen.
    """
    candidate = (path or "").strip().lstrip("/")
    if not candidate or "\\" in candidate or "\x00" in candidate:
        raise HTTPException(400, "Ungültiger Einkauf-Pfad")
    decoded = candidate
    for _ in range(3):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    segments = decoded.split("/")
    normalized = posixpath.normpath(decoded)
    if (
        any(segment in {"", ".", ".."} for segment in segments)
        or normalized != decoded
        or normalized.startswith("../")
        or normalized.startswith("/")
    ):
        raise HTTPException(400, "Pfadnavigation ist nicht erlaubt")
    top = normalized.split("/", 1)[0]
    if top not in _ALLOWED:
        raise HTTPException(404, f"Pfad nicht freigegeben: {top}")
    return normalized


def _configured_base_url() -> str:
    return (
        get_config().get("einkauf", "api_url", default="") or ""
    ).strip().rstrip("/")


def einkauf_configured() -> bool:
    return bool(_configured_base_url())


def _base_url() -> str:
    base = _configured_base_url()
    if not base:
        raise HTTPException(
            503,
            "Einkaufsliste nicht verbunden. Unter Admin → Einstellungen "
            "eine interne API-URL eintragen.",
        )
    return base


def _auth_headers() -> dict[str, str]:
    cfg = get_config()
    headers: dict[str, str] = {}

    app_token = (cfg.get("einkauf", "app_token", default="") or "").strip()
    if app_token:
        headers["x-app-token"] = app_token

    cf_id = (
        cfg.get("einkauf", "cf_access_client_id", default="") or ""
    ).strip()
    cf_secret = (
        cfg.get("einkauf", "cf_access_client_secret", default="") or ""
    ).strip()
    if cf_id and cf_secret:
        headers["CF-Access-Client-Id"] = cf_id
        headers["CF-Access-Client-Secret"] = cf_secret

    return headers


@router.get("/status")
def status() -> dict[str, Any]:
    """Konfigurationsstatus ohne Tokens oder andere Geheimnisse."""
    base = _configured_base_url()
    return {
        "configured": bool(base),
        "target": base,
    }


def einkauf_request(
    method: str,
    path: str,
    json: Any = None,
    timeout: tuple[int, int] = (5, 30),
) -> Any:
    """Server-seitiger Einkauf-Aufruf für interne Rezepte-Endpunkte."""
    safe_path = _validated_proxy_path(path)
    try:
        response = requests.request(
            method,
            f"{_base_url()}/{safe_path}",
            json=json,
            headers=_auth_headers(),
            timeout=timeout,
            allow_redirects=False,
        )
        response.raise_for_status()
    except HTTPException:
        raise
    except requests.RequestException as exc:
        logger.warning("Einkauf-Aufruf %s %s fehlgeschlagen: %s", method, path, exc)
        raise HTTPException(502, f"Einkaufsliste nicht erreichbar: {exc}") from exc
    return response.json() if response.content else None


@router.api_route("/{path:path}", methods=["GET", "POST", "PATCH", "DELETE"])
async def proxy(path: str, request: Request) -> Response:
    safe_path = _validated_proxy_path(path)
    url = f"{_base_url()}/{safe_path}"
    headers = _auth_headers()
    content_type = request.headers.get("content-type")
    if content_type:
        headers["content-type"] = content_type
    body = await request.body()
    params = dict(request.query_params)

    def _request() -> requests.Response:
        return requests.request(
            request.method,
            url,
            params=params,
            data=body or None,
            headers=headers,
            timeout=(5, 30),
            allow_redirects=False,
        )

    try:
        response = await run_in_threadpool(_request)
    except requests.RequestException as exc:
        logger.warning(
            "Einkauf-Proxy %s %s fehlgeschlagen: %s",
            request.method,
            safe_path,
            exc,
        )
        raise HTTPException(502, f"Einkaufsliste nicht erreichbar: {exc}") from exc

    media_type = response.headers.get("content-type", "application/json")
    media_type = media_type.split(";", 1)[0].strip()
    return Response(
        content=response.content,
        status_code=response.status_code,
        media_type=media_type,
    )
