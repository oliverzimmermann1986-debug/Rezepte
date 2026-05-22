"""Rate-Limiting für /login + Security-Header-Middleware.

Self-contained, keine zusätzlichen Dependencies.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Deque, Dict, Tuple

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response


class LoginRateLimiter:
    """Sliding-Window pro IP.

    Default: 5 Failed-Tries in 10 min  ->  15 min Sperre.
    """

    def __init__(self, max_fails: int = 5, window_sec: int = 600, ban_sec: int = 900):
        self.max_fails = max_fails
        self.window_sec = window_sec
        self.ban_sec = ban_sec
        self._fails: Dict[str, Deque[float]] = {}
        self._bans: Dict[str, float] = {}
        self._lock = threading.Lock()

    def is_blocked(self, ip: str) -> Tuple[bool, int]:
        """-> (blocked, remaining_seconds)."""
        now = time.time()
        with self._lock:
            until = self._bans.get(ip)
            if until and until > now:
                return True, int(until - now)
            if until:
                self._bans.pop(ip, None)
        return False, 0

    def record_fail(self, ip: str) -> None:
        now = time.time()
        with self._lock:
            dq = self._fails.setdefault(ip, deque())
            dq.append(now)
            cutoff = now - self.window_sec
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) >= self.max_fails:
                self._bans[ip] = now + self.ban_sec
                dq.clear()

    def record_success(self, ip: str) -> None:
        with self._lock:
            self._fails.pop(ip, None)
            self._bans.pop(ip, None)


# Singleton
login_limiter = LoginRateLimiter()


def client_ip(request: Request) -> str:
    """Echte Client-IP ermitteln. Bevorzugt CF-Connecting-IP."""
    cf = request.headers.get("cf-connecting-ip")
    if cf:
        return cf.strip()
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


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
            "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
            # Google Fonts (für JetBrains Mono + Space Grotesk)
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com data:; "
            "connect-src 'self'; "
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
