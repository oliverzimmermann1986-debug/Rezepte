"""Validation helpers for externally supplied media URLs."""
from __future__ import annotations

from urllib.parse import urlsplit

_ALLOWED_MEDIA_DOMAINS = ("tiktok.com", "instagram.com")


def is_supported_media_url(value: object) -> bool:
    """Accept only normal HTTP(S) TikTok/Instagram URLs without credentials.

    yt-dlp supports thousands of extractors, but this application is designed
    specifically for TikTok and Instagram. Keeping a strict allow-list prevents
    accidental internal/file URLs and option-like input from reaching yt-dlp.
    """
    raw = str(value or "").strip()
    if not raw or len(raw) > 4096 or any(ord(ch) < 32 for ch in raw):
        return False
    try:
        parsed = urlsplit(raw)
        host = (parsed.hostname or "").rstrip(".").lower()
        port = parsed.port
    except (ValueError, UnicodeError):
        return False
    if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        return False
    if port not in {None, 80, 443}:
        return False
    return any(host == domain or host.endswith("." + domain) for domain in _ALLOWED_MEDIA_DOMAINS)


def require_supported_media_url(value: object) -> str:
    raw = str(value or "").strip()
    if not is_supported_media_url(raw):
        raise ValueError("Nur normale TikTok- und Instagram-HTTP(S)-URLs sind erlaubt")
    return raw
