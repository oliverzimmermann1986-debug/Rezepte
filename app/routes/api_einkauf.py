"""Geschützter Reverse-Proxy zur bestehenden Einkaufsliste.

Die vollwertige Einkaufsliste bleibt ein eigener Dienst und behält ihre eigene
Datenbank. Das Rezepte-Frontend spricht nur diesen Proxy an; App- und
Cloudflare-Tokens bleiben dadurch ausschließlich auf dem Server.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import posixpath
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

import requests
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from ..auth import require_auth
from ..config_store import get_config
from ..core.webhook import pinned_https_request, validate_public_https_url

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

_DEFAULT_INTERNAL_EINKAUF_URLS = ("http://127.0.0.1:8010",)
_INTERNAL_URLS_ENV = "SCRAPPER_EINKAUF_INTERNAL_URLS"


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
    for _ in range(12):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    else:
        raise HTTPException(400, "Einkauf-Pfad ist zu oft kodiert")
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


def normalize_einkauf_base_url(value: str, *, status_code: int = 400) -> str:
    """Normalisiert die Basis-URL ohne DNS oder Netzwerkzugriff."""
    candidate = (value or "").strip()
    if not candidate or "\\" in candidate or "\x00" in candidate:
        raise HTTPException(status_code, "Ungültige Einkauf-API-URL")
    parsed = urlsplit(candidate)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code, "Einkauf-API-URL muss HTTP oder HTTPS verwenden")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HTTPException(
            status_code,
            "Einkauf-API-URL darf keine Zugangsdaten, Query oder Fragment enthalten",
        )
    try:
        hostname = parsed.hostname.rstrip(".").encode("idna").decode("ascii").casefold()
        port = parsed.port
    except (UnicodeError, ValueError) as exc:
        raise HTTPException(status_code, "Ungültiger Host oder Port der Einkauf-API") from exc
    path = parsed.path.rstrip("/")
    decoded_path = unquote(unquote(path))
    if any(segment in {".", ".."} for segment in decoded_path.split("/")):
        raise HTTPException(status_code, "Pfadnavigation in der Einkauf-API-URL ist nicht erlaubt")
    host_for_netloc = f"[{hostname}]" if ":" in hostname else hostname
    default_port = (parsed.scheme.lower() == "http" and port == 80) or (
        parsed.scheme.lower() == "https" and port == 443
    )
    netloc = host_for_netloc if port is None or default_port else f"{host_for_netloc}:{port}"
    return urlunsplit((parsed.scheme.lower(), netloc, path, "", ""))


def server_managed_internal_einkauf_urls() -> frozenset[str]:
    """Exakte interne Ziele aus Code-Default und serverseitiger Umgebung."""
    configured = list(_DEFAULT_INTERNAL_EINKAUF_URLS)
    configured.extend(
        part.strip()
        for part in os.environ.get(_INTERNAL_URLS_ENV, "").split(",")
        if part.strip()
    )
    allowed: set[str] = set()
    for value in configured:
        try:
            normalized = normalize_einkauf_base_url(value)
            parsed = urlsplit(normalized)
            address = ipaddress.ip_address(parsed.hostname or "")
        except (HTTPException, ValueError):
            logger.warning("Ungültiges internes Einkauf-Allowlist-Ziel ignoriert")
            continue
        if (
            address.is_global
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            logger.warning("Unsicheres internes Einkauf-Allowlist-Ziel ignoriert: %s", normalized)
            continue
        allowed.add(normalized)
    return frozenset(allowed)


def is_server_managed_internal_einkauf_url(value: str) -> bool:
    try:
        normalized = normalize_einkauf_base_url(value)
    except HTTPException:
        return False
    return normalized in server_managed_internal_einkauf_urls()


def validate_einkauf_base_url(
    value: str,
    *,
    status_code: int = 400,
    allow_server_managed_internal: bool = False,
    resolve_external: bool = True,
) -> str:
    """Erlaubt exakte Server-Internziele oder öffentliche HTTPS-Basis-URLs."""
    candidate = normalize_einkauf_base_url(value, status_code=status_code)
    if allow_server_managed_internal and is_server_managed_internal_einkauf_url(candidate):
        return candidate
    if urlsplit(candidate).scheme != "https":
        raise HTTPException(status_code, "Nur öffentliche HTTPS-Ziele sind erlaubt")
    if not resolve_external:
        return candidate
    try:
        validated = validate_public_https_url(candidate)
    except ValueError as exc:
        raise HTTPException(status_code, f"Unsichere Einkauf-API-URL: {exc}") from exc
    return normalize_einkauf_base_url(validated, status_code=status_code)


def einkauf_configured() -> bool:
    return bool(_configured_base_url())


def _base_url() -> str:
    base = _configured_base_url()
    if not base:
        raise HTTPException(
            503,
            "Einkaufsliste nicht verbunden. api_url serverseitig in "
            "config.yaml konfigurieren.",
        )
    return validate_einkauf_base_url(
        base,
        status_code=503,
        allow_server_managed_internal=True,
        resolve_external=False,
    )


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


def _send_einkauf_request(
    method: str,
    url: str,
    *,
    base_url: str,
    headers: dict[str, str],
    json: Any = None,
    data: Any = None,
    params: Any = None,
    timeout: tuple[int, int] = (5, 30),
) -> requests.Response:
    """Sendet intern an eine literal-IP oder extern DNS-gepinnt."""
    if is_server_managed_internal_einkauf_url(base_url):
        session = requests.Session()
        session.trust_env = False
        try:
            return session.request(
                method,
                url,
                json=json,
                data=data,
                params=params,
                headers=headers,
                timeout=timeout,
                allow_redirects=False,
            )
        finally:
            session.close()
    return pinned_https_request(
        method,
        url,
        json=json,
        data=data,
        params=params,
        headers=headers,
        timeout=timeout,
    )


def einkauf_response(
    method: str,
    path: str,
    *,
    json: Any = None,
    data: Any = None,
    params: Any = None,
    headers: dict[str, str] | None = None,
    timeout: tuple[int, int] = (5, 30),
) -> requests.Response:
    """Zentraler Transport für Proxy und Cart-Push."""
    safe_path = _validated_proxy_path(path)
    base = _base_url()
    outbound_headers = _auth_headers()
    outbound_headers.update(headers or {})
    try:
        return _send_einkauf_request(
            method,
            f"{base}/{safe_path}",
            base_url=base,
            json=json,
            data=data,
            params=params,
            headers=outbound_headers,
            timeout=timeout,
        )
    except ValueError as exc:
        raise HTTPException(503, f"Unsichere Einkauf-API-URL: {exc}") from exc


def einkauf_request(
    method: str,
    path: str,
    json: Any = None,
    timeout: tuple[int, int] = (5, 30),
) -> Any:
    """Server-seitiger Einkauf-Aufruf für interne Rezepte-Endpunkte."""
    try:
        response = einkauf_response(
            method,
            path,
            json=json,
            timeout=timeout,
        )
        if 300 <= response.status_code < 400:
            raise HTTPException(502, "Redirect der Einkaufsliste wurde blockiert")
        response.raise_for_status()
    except HTTPException:
        raise
    except requests.RequestException as exc:
        logger.warning("Einkauf-Aufruf %s %s fehlgeschlagen: %s", method, path, exc)
        raise HTTPException(502, f"Einkaufsliste nicht erreichbar: {exc}") from exc
    return response.json() if response.content else None


@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PATCH", "DELETE"],
    # Opaquer Legacy-Proxy ohne festes Request-/Response-Schema. In OpenAPI
    # würde dieselbe Funktion für vier Verben doppelte Operation-IDs erzeugen.
    include_in_schema=False,
)
async def proxy(path: str, request: Request) -> Response:
    safe_path = _validated_proxy_path(path)
    headers: dict[str, str] = {}
    content_type = request.headers.get("content-type")
    if content_type:
        headers["content-type"] = content_type
    body = await request.body()
    params = dict(request.query_params)

    def _request() -> requests.Response:
        return einkauf_response(
            request.method,
            safe_path,
            params=params,
            data=body or None,
            headers=headers,
            timeout=(5, 30),
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

    if 300 <= response.status_code < 400:
        raise HTTPException(502, "Redirect der Einkaufsliste wurde blockiert")

    media_type = response.headers.get("content-type", "application/json")
    media_type = media_type.split(";", 1)[0].strip()
    return Response(
        content=response.content,
        status_code=response.status_code,
        media_type=media_type,
    )
