"""Regressionstests für den Open-Redirect-Schutz in _safe_next.

Hintergrund: '/\\evil.com' passiert einen reinen '//'-Check, wird von Browsern
aber zu '//evil.com' normalisiert (scheme-relativer Redirect auf fremde Domain).
Zusätzlich werden Steuerzeichen (Header-Injection) und Überlänge abgewehrt.
"""
from __future__ import annotations

from app.main import _safe_next


def test_plain_local_path_passes():
    assert _safe_next("/recipes") == "/recipes"
    assert _safe_next("/#recipes") == "/#recipes"


def test_protocol_relative_blocked():
    assert _safe_next("//evil.com") == "/"


def test_backslash_bypass_blocked():
    # Browser normalisiert '\' -> '/', daraus wuerde '//evil.com'
    assert _safe_next("/\\evil.com") == "/"
    assert _safe_next("\\\\evil.com") == "/"


def test_absolute_url_blocked():
    assert _safe_next("https://evil.com") == "/"
    assert _safe_next("evil.com") == "/"


def test_control_chars_blocked():
    assert _safe_next("/foo\r\nSet-Cookie: x=1") == "/"
    assert _safe_next("/foo\x00bar") == "/"


def test_empty_falls_back_to_root():
    assert _safe_next("") == "/"
    assert _safe_next(None) == "/"  # type: ignore[arg-type]
