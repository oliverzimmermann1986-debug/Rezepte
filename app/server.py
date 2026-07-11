"""Produktions-Entry-Point für Uvicorn.

Liest Bind-Adresse, Port und vertrauenswürdige Reverse-Proxies aus config.yaml,
damit die Web-Einstellungen tatsächlich wirksam sind und Forwarded-Header nicht
von beliebigen LAN-/Internet-Clients gefälscht werden können.
"""
from __future__ import annotations

import logging

import uvicorn

from .config_store import get_config

logger = logging.getLogger(__name__)


def _trusted_proxies(value) -> str:
    if isinstance(value, str):
        items = [x.strip() for x in value.split(",") if x.strip()]
    elif isinstance(value, list):
        items = [str(x).strip() for x in value if str(x).strip()]
    else:
        items = []
    # Loopback ist für lokalen cloudflared/nginx immer sicher und praktisch.
    for loopback in ("127.0.0.1", "::1"):
        if loopback not in items:
            items.append(loopback)
    return ",".join(items)


def main() -> None:
    cfg = get_config()
    host = str(cfg.get("web", "bind_host", default="127.0.0.1") or "127.0.0.1").strip()
    port = int(cfg.get("web", "bind_port", default=8000) or 8000)
    forwarded_allow_ips = _trusted_proxies(cfg.get("web", "trusted_proxies", default=[]))
    logger.info("Starte Webserver auf %s:%s; trusted proxies=%s", host, port, forwarded_allow_ips)
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        proxy_headers=True,
        forwarded_allow_ips=forwarded_allow_ips,
        access_log=True,
    )


if __name__ == "__main__":
    main()
