"""Webhook-Notifier (Telegram-Ersatz).

Generischer HTTPS-POST mit JSON-Payload. User trägt eine oder mehrere
öffentlich erreichbare Ziel-URLs ein und wählt für jede die Events.
Private, lokale und Link-Local-Ziele werden als SSRF-Schutz blockiert.
Funktioniert mit:
  - Discord-Webhooks (https://discord.com/api/webhooks/...)
  - ntfy.sh / öffentliches ntfy
  - Slack-Incoming-Webhooks
  - Microsoft Teams
  - Öffentliche eigene Endpoints (z.B. n8n)

Format:
  {
    "event": "scraper_done" | "job_failed" | "pending_high",
    "timestamp": "2026-05-22T15:30:00+00:00",
    "host": "scrapper",
    "summary": { ... event-spezifisch ... }
  }

Fire-and-forget über einen Modul-globalen ThreadPool, wie der frühere
Telegram-Notifier. Wenn ein Webhook tot ist blockiert er nicht den Job.
"""
from __future__ import annotations

import atexit
import ipaddress
import json
import logging
import socket
import ssl
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Collection, Dict, List, Mapping
from urllib.parse import unquote, urlsplit, urlunsplit

import requests
import urllib3
from urllib3.util import Timeout

logger = logging.getLogger(__name__)


# Globaler Pool. 2 Worker sind genug - Webhooks gehen schnell oder fail-fast.
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="webhook")


@dataclass(frozen=True)
class ResolvedHttpsTarget:
    """Ein einmalig aufgelöstes, ausschließlich öffentliches HTTPS-Ziel."""

    url: str
    hostname: str
    port: int
    addresses: tuple[str, ...]
    host_header: str


def resolve_public_https_url(url: str) -> ResolvedHttpsTarget:
    """Validiert ein ausgehendes Ziel gegen SSRF und Secret-Abfluss.

    Webhook-URLs dürfen Query-Parameter enthalten (Teams und andere Anbieter
    tragen dort Tokens), müssen aber öffentlich auflösbares HTTPS verwenden.
    Alle aufgelösten Adressen werden geprüft; ein Host mit auch nur einer
    privaten, Loopback-, Link-Local- oder reservierten Adresse wird verworfen.
    Das Ergebnis enthält genau diese geprüften IPs, damit der Request nicht
    erneut per DNS auflöst und dadurch DNS-Rebinding möglich macht.
    """
    candidate = (url or "").strip()
    if not candidate or "\\" in candidate or "\x00" in candidate:
        raise ValueError("Ungültige Ziel-URL")
    parsed = urlsplit(candidate)
    if parsed.scheme.lower() != "https":
        raise ValueError("Nur öffentliche HTTPS-Ziele sind erlaubt")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Ziel-URL darf keine eingebetteten Zugangsdaten enthalten")
    try:
        hostname = parsed.hostname.rstrip(".").encode("idna").decode("ascii").casefold()
    except UnicodeError as exc:
        raise ValueError("Ungültiger Hostname in der Ziel-URL") from exc
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ValueError("Lokale Zieladressen sind nicht erlaubt")
    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise ValueError("Ungültiger Port in der Ziel-URL") from exc
    try:
        resolved = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("Host der Ziel-URL konnte nicht aufgelöst werden") from exc
    addresses = tuple(dict.fromkeys(info[4][0].split("%", 1)[0] for info in resolved))
    if not addresses:
        raise ValueError("Host der Ziel-URL konnte nicht aufgelöst werden")
    for raw in addresses:
        try:
            address = ipaddress.ip_address(raw)
        except ValueError as exc:
            raise ValueError("Host lieferte eine ungültige IP-Adresse") from exc
        if not address.is_global:
            raise ValueError("Private oder lokale Zieladressen sind nicht erlaubt")
    host_for_header = f"[{hostname}]" if ":" in hostname else hostname
    host_header = host_for_header if port == 443 else f"{host_for_header}:{port}"
    return ResolvedHttpsTarget(
        url=candidate,
        hostname=hostname,
        port=port,
        addresses=addresses,
        host_header=host_header,
    )


def validate_public_https_url(url: str) -> str:
    """Kompatibilitäts-API: validiert und liefert die normalisierte Eingabe."""
    return resolve_public_https_url(url).url


def _transport_timeout(value: Any) -> Timeout:
    if isinstance(value, tuple):
        return Timeout(connect=float(value[0]), read=float(value[1]))
    if value is None:
        return Timeout(connect=None, read=None)
    return Timeout.from_float(float(value))


def _response_from_urllib3(
    raw: Any,
    prepared: requests.PreparedRequest,
    *,
    content: bytes | None = None,
) -> requests.Response:
    response = requests.Response()
    response.status_code = int(raw.status)
    response.headers = requests.structures.CaseInsensitiveDict(raw.headers)
    response._content = raw.data if content is None else content
    response.url = prepared.url
    response.reason = raw.reason
    response.request = prepared
    response.encoding = requests.utils.get_encoding_from_headers(response.headers)
    response.raw = raw
    return response


def pinned_https_request(
    method: str,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    json: Any = None,
    data: Any = None,
    files: Any = None,
    params: Any = None,
    timeout: Any = 10,
    max_response_bytes: int | None = None,
) -> requests.Response:
    """HTTPS-Request ohne zweite DNS-Auflösung.

    Die Verbindung wird an eine zuvor validierte öffentliche IP gebunden.
    TLS-SNI und Zertifikatsprüfung bleiben auf dem ursprünglichen Hostnamen;
    ebenso wird der korrekte ``Host``-Header gesendet. Redirects und urllib3-
    Retries sind deaktiviert, damit kein ungeprüftes Folgeziel kontaktiert wird.
    """
    session = requests.Session()
    session.trust_env = False
    try:
        prepared = session.prepare_request(
            requests.Request(
                method=method,
                url=url,
                headers=dict(headers or {}),
                json=json,
                data=data,
                files=files,
                params=params,
            )
        )
    finally:
        session.close()

    target = resolve_public_https_url(prepared.url or url)
    parsed = urlsplit(prepared.url or url)
    request_target = parsed.path or "/"
    if parsed.query:
        request_target = f"{request_target}?{parsed.query}"
    outbound_headers = dict(prepared.headers)
    outbound_headers["Host"] = target.host_header
    transport_timeout = _transport_timeout(timeout)

    last_error: Exception | None = None
    for address in target.addresses:
        pool = urllib3.HTTPSConnectionPool(
            address,
            port=target.port,
            maxsize=1,
            retries=False,
            cert_reqs=ssl.CERT_REQUIRED,
            ca_certs=requests.certs.where(),
            assert_hostname=target.hostname,
            server_hostname=target.hostname,
        )
        try:
            raw = pool.urlopen(
                prepared.method or method,
                request_target,
                body=prepared.body,
                headers=outbound_headers,
                redirect=False,
                retries=False,
                assert_same_host=False,
                timeout=transport_timeout,
                preload_content=max_response_bytes is None,
                decode_content=True,
            )
            if max_response_bytes is not None:
                limit = max(1, int(max_response_bytes))
                content = raw.read(limit + 1, decode_content=True)
                if len(content) > limit:
                    raise ValueError(
                        f"Antwort überschreitet das Limit von {limit} Bytes"
                    )
                return _response_from_urllib3(raw, prepared, content=content)
            return _response_from_urllib3(raw, prepared)
        except urllib3.exceptions.HTTPError as exc:
            last_error = exc
        finally:
            pool.close()
    message = f"Keine geprüfte Adresse für {target.hostname} war erreichbar"
    if isinstance(last_error, urllib3.exceptions.TimeoutError):
        raise requests.Timeout(message) from last_error
    raise requests.ConnectionError(message) from last_error


def normalize_server_base_url(value: str) -> str:
    """Kanonische HTTP(S)-Basis-URL für serververwaltete Trust-Listen."""
    candidate = (value or "").strip()
    if not candidate or "\\" in candidate or "\x00" in candidate:
        raise ValueError("Ungültige serververwaltete URL")
    parsed = urlsplit(candidate)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Serververwaltete URL muss HTTP oder HTTPS verwenden")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Serververwaltete URL darf keine Credentials, Query oder Fragment enthalten")
    try:
        hostname = parsed.hostname.rstrip(".").encode("idna").decode("ascii").casefold()
        port = parsed.port
    except (UnicodeError, ValueError) as exc:
        raise ValueError("Ungültiger Host oder Port") from exc
    decoded_path = unquote(unquote(parsed.path))
    if "\\" in decoded_path or any(
        segment in {".", ".."} for segment in decoded_path.split("/")
    ):
        raise ValueError("Pfadnavigation ist nicht erlaubt")
    host_for_netloc = f"[{hostname}]" if ":" in hostname else hostname
    default_port = (parsed.scheme.lower() == "http" and port == 80) or (
        parsed.scheme.lower() == "https" and port == 443
    )
    netloc = host_for_netloc if port is None or default_port else f"{host_for_netloc}:{port}"
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path.rstrip("/"), "", ""))


def _matches_trusted_private_base(url: str, trusted_bases: Collection[str]) -> bool:
    parsed = urlsplit(url)
    try:
        hostname = parsed.hostname or ""
        address = ipaddress.ip_address(hostname)
        request_port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError:
        return False
    if (
        address.is_global
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        return False
    request_path = unquote(unquote(parsed.path or "/"))
    if "\\" in request_path or any(
        segment in {".", ".."} for segment in request_path.split("/")
    ):
        return False

    for raw_base in trusted_bases:
        try:
            base = urlsplit(normalize_server_base_url(raw_base))
            base_address = ipaddress.ip_address(base.hostname or "")
            base_port = base.port or (443 if base.scheme == "https" else 80)
        except ValueError:
            continue
        base_path = unquote(unquote(base.path.rstrip("/")))
        path_matches = request_path == (base_path or "/") or request_path.startswith(
            f"{base_path}/" if base_path else "/"
        )
        if (
            address == base_address
            and parsed.scheme.lower() == base.scheme
            and request_port == base_port
            and path_matches
        ):
            return True
    return False


def server_configured_request(
    method: str,
    url: str,
    *,
    trusted_private_bases: Collection[str] = (),
    headers: Mapping[str, str] | None = None,
    json: Any = None,
    data: Any = None,
    files: Any = None,
    params: Any = None,
    timeout: Any = 10,
    max_response_bytes: int | None = None,
) -> requests.Response:
    """Öffentlich DNS-gepinnt, intern nur als exakte Literal-IP-Allowlist.

    ``trusted_private_bases`` darf ausschließlich aus serververwalteter,
    nicht per Request änderbarer Konfiguration stammen. Private Hostnamen
    werden nie akzeptiert; damit kann DNS-Rebinding nicht in die interne
    Trust-Grenze wechseln.
    """
    candidate = (url or "").strip()
    if not candidate or "\\" in candidate or "\x00" in candidate:
        raise ValueError("Ungültige Ziel-URL")
    parsed = urlsplit(candidate)
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Ungültige Ziel-URL")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None

    if address is not None and not address.is_global:
        if not _matches_trusted_private_base(candidate, trusted_private_bases):
            raise ValueError("Internes Ziel ist nicht serverseitig freigegeben")
        session = requests.Session()
        session.trust_env = False
        try:
            return session.request(
                method,
                candidate,
                headers=dict(headers or {}),
                json=json,
                data=data,
                files=files,
                params=params,
                timeout=timeout,
                allow_redirects=False,
            )
        finally:
            session.close()

    return pinned_https_request(
        method,
        candidate,
        headers=headers,
        json=json,
        data=data,
        files=files,
        params=params,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
    )


def _shutdown_pool() -> None:
    _POOL.shutdown(wait=False, cancel_futures=True)


atexit.register(_shutdown_pool)


# Discord erkennt manche Felder spezifisch. Wir bauen für Discord ein
# 'embeds'-Format zusätzlich zum 'content', damit's gut aussieht.
def _format_discord(payload: dict) -> dict:
    color_by_event = {
        "scraper_done": 0x22c55e,
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
    name = target.get("name") or urlsplit(url).hostname or "webhook"
    try:
        body = _detect_format(url, payload)
        r = pinned_https_request(
            "POST",
            url,
            json=body,
            timeout=10,
        )
        if 200 <= r.status_code < 300:
            logger.info(f"webhook[{name}] {r.status_code} ok")
            return True
        if 300 <= r.status_code < 400:
            logger.warning("webhook[%s] Redirect blockiert", name)
            return False
        logger.warning("webhook[%s] HTTP %s", name, r.status_code)
        return False
    except Exception as e:
        logger.error(f"webhook[{name}] failed: {e}")
        return False


def notify(event: str, summary: dict, *, sync: bool = False) -> List[dict]:
    """Sendet ein Event an alle konfigurierten Webhooks, die das Event abonniert haben.

    Args:
        event:    'scraper_done' | 'job_failed' | 'pending_high'
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
        events = hook.get("events") or ["scraper_done", "job_failed"]
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
