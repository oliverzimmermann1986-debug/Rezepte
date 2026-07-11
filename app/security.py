"""Web-Sicherheitsmiddleware und Login-Rate-Limiting."""
from __future__ import annotations

import ipaddress
import threading
import time
from collections import deque
from typing import Deque, Dict, Tuple
from urllib.parse import urlsplit

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response


class LoginRateLimiter:
    """Sliding-Window pro IP mit begrenztem In-Memory-State."""

    def __init__(self, max_fails: int = 5, window_sec: int = 600, ban_sec: int = 900,
                 max_entries: int = 10_000):
        self.max_fails = max_fails
        self.window_sec = window_sec
        self.ban_sec = ban_sec
        self.max_entries = max_entries
        self._fails: Dict[str, Deque[float]] = {}
        self._bans: Dict[str, float] = {}
        self._lock = threading.Lock()

    def _cleanup(self, now: float) -> None:
        cutoff = now - self.window_sec
        for ip in list(self._fails):
            dq = self._fails[ip]
            while dq and dq[0] < cutoff:
                dq.popleft()
            if not dq:
                self._fails.pop(ip, None)
        for ip, until in list(self._bans.items()):
            if until <= now:
                self._bans.pop(ip, None)
        # Schutz gegen Memory-DoS durch Millionen gefälschte IPs.
        if len(self._fails) > self.max_entries:
            oldest = sorted(self._fails, key=lambda k: self._fails[k][-1])
            for ip in oldest[:len(self._fails) - self.max_entries]:
                self._fails.pop(ip, None)

    def is_blocked(self, ip: str) -> Tuple[bool, int]:
        now = time.time()
        with self._lock:
            self._cleanup(now)
            until = self._bans.get(ip)
            if until and until > now:
                return True, max(1, int(until - now))
        return False, 0

    def record_fail(self, ip: str) -> None:
        now = time.time()
        with self._lock:
            self._cleanup(now)
            dq = self._fails.setdefault(ip, deque())
            dq.append(now)
            if len(dq) >= self.max_fails:
                self._bans[ip] = now + self.ban_sec
                dq.clear()

    def record_success(self, ip: str) -> None:
        with self._lock:
            self._fails.pop(ip, None)
            self._bans.pop(ip, None)


login_limiter = LoginRateLimiter()


def _origin_endpoint(value: str, *, default_scheme: str = "") -> tuple[str, str, int | None] | None:
    """Parst Origin/Host kanonisch, inklusive IPv6 und Default-Ports."""
    try:
        parsed = urlsplit(value if "://" in value else f"//{value}", scheme=default_scheme)
        scheme = (parsed.scheme or default_scheme).lower()
        host = (parsed.hostname or "").lower().rstrip(".")
        if not scheme or not host:
            return None
        port = parsed.port
        if port is None:
            port = 443 if scheme == "https" else 80 if scheme == "http" else None
        return scheme, host, port
    except (ValueError, TypeError):
        return None


def client_ip(request: Request) -> str:
    """Liefert die von Uvicorn bereits validierte Client-IP.

    Forwarded-Header werden absichtlich *nicht* hier geparst. Welche Proxies
    vertrauenswürdig sind, entscheidet ``app.server`` über Uvicorns
    ``forwarded_allow_ips``. Direkte Clients können dadurch keine IP vortäuschen.
    """
    raw = request.client.host if request.client else "unknown"
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return str(raw)[:128]


class SameOriginMiddleware(BaseHTTPMiddleware):
    """Blockiert fremde Browser-Origins bei schreibenden Requests.

    Requests ohne Origin (CLI, systemd, native Clients) bleiben erlaubt. Der
    Session-Cookie ist zusätzlich SameSite=Lax; dies ist Defense in Depth.
    """

    async def dispatch(self, request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin:
                origin_endpoint = _origin_endpoint(origin)
                request_endpoint = _origin_endpoint(
                    request.headers.get("host", ""),
                    default_scheme=request.url.scheme,
                )
                if origin_endpoint is None or origin_endpoint != request_endpoint:
                    return JSONResponse({"detail": "Cross-origin request blocked"}, status_code=403)
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Setzt sichere Default-Header. CSP bleibt mit Alpine.js kompatibel."""

    async def dispatch(self, request: Request, call_next):
        resp: Response = await call_next(request)
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        resp.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        resp.headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), microphone=(), camera=(), payment=(), usb=()",
        )
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "img-src 'self' data: blob:; "
            "media-src 'self' blob:; "
            # Alpine 3 wertet x-* Expressions dynamisch aus und benötigt unsafe-eval.
            # Inline-Skripte sind nicht nötig und deshalb nicht erlaubt.
            "script-src 'self' 'unsafe-eval'; "
            # Das bestehende UI nutzt Inline-Styles; daher vorerst unsafe-inline.
            "style-src 'self' 'unsafe-inline'; "
            "font-src 'self' data:; "
            "connect-src 'self'; "
            "object-src 'none'; frame-src 'self'; worker-src 'self' blob:; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
        )
        if request.url.scheme == "https":
            resp.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        if request.url.path.startswith("/api/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp
