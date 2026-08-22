"""Statische Sicherheitsregressionen für die ausgelieferten Units."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_web_service_never_trusts_forwarded_headers_from_every_peer():
    unit = (ROOT / "systemd" / "scrapper-web.service").read_text(encoding="utf-8")
    assert "--forwarded-allow-ips=*" not in unit
    assert "SCRAPPER_FORWARDED_ALLOW_IPS=127.0.0.1" in unit
    assert "--forwarded-allow-ips=${SCRAPPER_FORWARDED_ALLOW_IPS}" in unit
    assert "EnvironmentFile=-/opt/scrapper/data/web.env" in unit

