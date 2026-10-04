"""Persistente 24-Stunden-Grenzen für alle kostenpflichtigen KI-Versuche."""
from __future__ import annotations

import time

from .config_store import get_config
from .db import get_db


class AIBudgetExceeded(RuntimeError):
    pass


def _limit(config, key: str, default: int) -> int:
    value = config.get("ai", "openai", key, default=default)
    # Falsche Werte sperren Aufrufe, anstatt eine Kostenbremse abzuschalten.
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 100_000 else 0


def reserve_request(method: str, path: str) -> None:
    if method.upper() != "POST":
        return
    kind = "image" if path.rstrip("/") == "/images/generations" else "analysis"
    config = get_config()
    total_limit = _limit(config, "server_daily_request_limit", 1000)
    image_limit = _limit(config, "server_daily_image_limit", 40)
    now = time.time()
    with get_db().conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM ai_request_budget_usage WHERE created_at<=?", (now - 86400,))
        total, images = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(kind='image'),0) FROM ai_request_budget_usage"
        ).fetchone()
        if total >= total_limit or (kind == "image" and images >= image_limit):
            raise AIBudgetExceeded("KI-Tageskontingent erreicht; bitte später erneut versuchen")
        # Auch Timeouts und Wiederholungen zählen: der Anbieter kann einen
        # Versuch trotz verlorener Antwort verarbeitet haben.
        conn.execute("INSERT INTO ai_request_budget_usage(created_at,kind) VALUES(?,?)", (now, kind))
