"""Rate-Limiting für /login + Security-Header-Middleware.

Self-contained, keine zusätzlichen Dependencies.
"""
from __future__ import annotations

import ipaddress
import threading
import time
from collections import deque
from typing import Any, Awaitable, Callable, Deque, Dict, Iterable, Optional, Tuple
from urllib.parse import urlsplit

from fastapi import Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response


class LoginRateLimiter:
    """Sliding-Window pro IP.

    Default: 5 Failed-Tries in 10 min  ->  15 min Sperre.
    """

    def __init__(
        self,
        max_fails: int = 5,
        window_sec: int = 600,
        ban_sec: int = 900,
        max_entries: int = 10_000,
    ):
        self.max_fails = max_fails
        self.window_sec = window_sec
        self.ban_sec = ban_sec
        self.max_entries = max(100, int(max_entries))
        self._fails: Dict[str, Deque[float]] = {}
        self._bans: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._last_prune = 0.0

    def _prune_locked(self, now: float, *, force: bool = False) -> None:
        """Entfernt abgelaufene Einträge und begrenzt fremde IP-Schlüssel.

        Ohne Limit kann ein Angreifer durch ständig neue Forwarded-Header das
        Prozess-RAM unbegrenzt wachsen lassen.
        """
        if not force and now - self._last_prune < 30:
            return
        self._last_prune = now
        cutoff = now - self.window_sec

        for ip, dq in list(self._fails.items()):
            while dq and dq[0] < cutoff:
                dq.popleft()
            if not dq:
                self._fails.pop(ip, None)
        for ip, until in list(self._bans.items()):
            if until <= now:
                self._bans.pop(ip, None)

        keys = set(self._fails) | set(self._bans)
        overflow = len(keys) - self.max_entries
        if overflow <= 0:
            return
        oldest = sorted(
            keys,
            key=lambda ip: max(
                self._bans.get(ip, 0.0),
                self._fails[ip][-1] if self._fails.get(ip) else 0.0,
            ),
        )
        for ip in oldest[:overflow]:
            self._fails.pop(ip, None)
            self._bans.pop(ip, None)

    def is_blocked(self, ip: str) -> Tuple[bool, int]:
        """-> (blocked, remaining_seconds)."""
        now = time.time()
        with self._lock:
            self._prune_locked(now)
            until = self._bans.get(ip)
            if until and until > now:
                return True, int(until - now)
            if until:
                self._bans.pop(ip, None)
        return False, 0

    def record_fail(self, ip: str) -> None:
        now = time.time()
        with self._lock:
            self._prune_locked(now)
            dq = self._fails.setdefault(ip, deque())
            dq.append(now)
            cutoff = now - self.window_sec
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) >= self.max_fails:
                self._bans[ip] = now + self.ban_sec
                dq.clear()
            self._prune_locked(now, force=len(set(self._fails) | set(self._bans)) > self.max_entries)

    def record_success(self, ip: str) -> None:
        with self._lock:
            self._fails.pop(ip, None)
            self._bans.pop(ip, None)


# Singleton
login_limiter = LoginRateLimiter()


class _UploadBodyTooLarge(Exception):
    pass


class UploadSizeLimitMiddleware:
    """Begrenzt Multipart-Bodies beim ASGI-Einlesen vor dem Form-Parser.

    FastAPI erzeugt ``UploadFile`` erst nach dem Multipart-Parsing. Eine reine
    Größenprüfung im Endpoint kommt deshalb zu spät und kann die Platte bereits
    mit einer großen Spool-Datei belegt haben.
    """

    _OVERHEAD = 1024 * 1024

    def __init__(self, app: Callable[..., Awaitable[Any]]):
        self.app = app

    @classmethod
    def _limit_for(cls, path: str) -> Optional[int]:
        if path == "/api/pending/import-file":
            return 25 * 1024 * 1024 + cls._OVERHEAD
        if path == "/api/pending/scan-photo" or path.endswith("/upload-thumbnail"):
            return 10 * 1024 * 1024 + cls._OVERHEAD
        return None

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = {
            key.lower(): value
            for key, value in scope.get("headers", [])
        }
        content_type = headers.get(b"content-type", b"").lower()
        limit = self._limit_for(str(scope.get("path") or ""))
        if limit is None or not content_type.startswith(b"multipart/form-data"):
            await self.app(scope, receive, send)
            return
        try:
            content_length = int(headers.get(b"content-length", b"0") or b"0")
        except ValueError:
            content_length = 0
        if content_length > limit:
            response = JSONResponse({"detail": "Upload zu groß"}, status_code=413)
            await response(scope, receive, send)
            return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body") or b"")
                if received > limit:
                    raise _UploadBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _UploadBodyTooLarge:
            response = JSONResponse({"detail": "Upload zu groß"}, status_code=413)
            await response(scope, receive, send)


def _parsed_ip(value: str) -> Optional[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        return ipaddress.ip_address((value or "").strip())
    except ValueError:
        return None


def _trusted_proxy_networks() -> Iterable[
    ipaddress.IPv4Network | ipaddress.IPv6Network
]:
    """Konfigurierte Proxy-Netze; Default ist ausschließlich localhost."""
    try:
        from .config_store import get_config
        raw = get_config().get(
            "web",
            "trusted_proxies",
            default=["127.0.0.1/32", "::1/128"],
        )
    except Exception:
        raw = ["127.0.0.1/32", "::1/128"]
    if isinstance(raw, str):
        raw = [item.strip() for item in raw.split(",") if item.strip()]
    if not isinstance(raw, list):
        raw = []
    for item in raw:
        try:
            yield ipaddress.ip_network(str(item).strip(), strict=False)
        except ValueError:
            continue


def client_ip(request: Request) -> str:
    """Client-IP ermitteln, Forwarded-Header nur von bekannten Proxies.

    Direkte Clients dürfen ``CF-Connecting-IP`` und ``X-Forwarded-For`` nicht
    zur Umgehung des Login-Limits selbst bestimmen.
    """
    peer_text = request.client.host if request.client else ""
    peer = _parsed_ip(peer_text)
    trusted_peer = bool(
        peer and any(peer in network for network in _trusted_proxy_networks())
    )
    if trusted_peer:
        for candidate in (
            request.headers.get("cf-connecting-ip", ""),
            request.headers.get("x-forwarded-for", "").split(",")[0],
        ):
            parsed = _parsed_ip(candidate)
            if parsed:
                return str(parsed)
    return str(peer) if peer else "unknown"


def request_is_from_trusted_proxy(request: Request) -> bool:
    """Ob der unmittelbare TCP-Peer als lokale Auth-Grenze konfiguriert ist.

    Im ``auth_disabled``-Betrieb darf nicht schon die bloße Erreichbarkeit des
    Uvicorn-Ports Administratorrechte verleihen. Standardmäßig sind daher nur
    Loopback-Peers zugelassen; weitere Proxy-Netze müssen explizit konfiguriert
    werden.
    """
    peer_text = request.client.host if request.client else ""
    peer = _parsed_ip(peer_text)
    return bool(peer and any(peer in network for network in _trusted_proxy_networks()))


def _same_origin(request: Request) -> bool:
    """Validiert Origin/Referer gegen das tatsächlich adressierte Host-Header-Ziel."""
    source = (request.headers.get("origin") or "").strip()
    if not source:
        source = (request.headers.get("referer") or "").strip()
    if not source:
        return False
    try:
        parsed = urlsplit(source)
        source_host = parsed.netloc.casefold()
    except ValueError:
        return False
    request_host = (request.headers.get("host") or "").strip().casefold()
    if not parsed.scheme or not source_host or source_host != request_host:
        return False
    forwarded_proto = (request.headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip()
    expected_scheme = forwarded_proto if request_is_from_trusted_proxy(request) and forwarded_proto else request.url.scheme
    return parsed.scheme.casefold() == expected_scheme.casefold()


class SameOriginMiddleware(BaseHTTPMiddleware):
    """Blockiert Cookie-/Form-CSRF, einschließlich Same-Site-Sibling-Angriffen.

    Bearer-Requests der nativen App besitzen keine ambienten Browser-Cookies
    und bleiben ohne Origin-Header zulässig. Der Login-POST wird immer geprüft,
    weil er Login-CSRF sonst vor dem Setzen des Cookies erlauben würde.
    """

    async def dispatch(self, request: Request, call_next):
        if request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            has_cookie_auth = bool(request.cookies.get("scrapper_session"))
            origin_required_path = request.url.path in {"/login", "/share/resolve"}
            has_bearer = request.headers.get("authorization", "").lower().startswith("bearer ")
            if (origin_required_path or has_cookie_auth) and not has_bearer and not _same_origin(request):
                return JSONResponse(
                    {"detail": "Ungültige Anfrageherkunft"},
                    status_code=status.HTTP_403_FORBIDDEN,
                    headers={"Cache-Control": "no-store"},
                )
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Setzt sichere Default-Header. CSP ist mit Alpine.js kompatibel."""

    async def dispatch(self, request: Request, call_next):
        resp: Response = await call_next(request)
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), microphone=(), camera=(), payment=()",
        )
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "img-src 'self' data: blob:; "
            "media-src 'self' blob:; "
            # 'unsafe-eval' ist nötig weil Alpine.js intern new Function()
            # nutzt um x-show/x-text/x-bind/@click-Expressions auszuwerten.
            # Ohne das zerlegt es das ganze Frontend (Modals öffnen sich
            # unkontrolliert, Buttons reagieren nicht).
            "script-src 'self' 'unsafe-eval'; "
            "style-src 'self' 'unsafe-inline'; "
            "font-src 'self' data:; "
            "connect-src 'self'; "
            # PWA: Service Worker + Manifest
            "worker-src 'self'; "
            "manifest-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'",
        )
        # HSTS nur wenn wir hinter HTTPS sitzen
        proto = request.headers.get("x-forwarded-proto", "").lower()
        if proto == "https" or request.url.scheme == "https":
            resp.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return resp
