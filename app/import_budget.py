"""Persistent rolling limits before user-triggered download/AI work starts."""
from __future__ import annotations

import math
import time

from fastapi import HTTPException

from .config_store import get_config

WINDOW_SECONDS = 24 * 60 * 60


def _limit(config, name, default):
    try:
        return max(0, int(config.get("web", name, default=default)))
    except (TypeError, ValueError):
        return default


def reserve_import(db, *, dedupe_key=None, kind="share_ingest"):
    """Count attempted analysis/images, including potentially costly failures.

    Return True for a new reservation, False for an active replay. Queued
    replays must pass reserve_budget=True to enqueue: its transaction either
    returns that active job or reserves a slot for a newly needed job.
    Global recipe references and upload replays return before this function.
    """
    with db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        if dedupe_key and c.execute("SELECT 1 FROM background_tasks WHERE kind=? "
                                   "AND dedupe_key=? AND status IN ('queued','running') LIMIT 1", (kind, dedupe_key)).fetchone():
            return False
        reserve_in_connection(db, c)
        return True


def reserve_in_connection(db, c):
    """Reserve using the caller's writer transaction, including queue insertion."""
    from .tenancy import CURRENT_HOUSEHOLD
    scope = CURRENT_HOUSEHOLD.get() or getattr(db, "scope", None)
    account_id = scope.account_id if scope and scope.account_id > 0 else 0
    config = get_config()
    per_account = _limit(config, "import_daily_limit", 20)
    server_limit = _limit(config, "import_server_daily_limit", 200)
    now = time.time()
    c.execute("DELETE FROM import_budget_usage WHERE created_at<=?", (now-WINDOW_SECONDS,))
    for where, params, limit, message in (
        ("account_id=?", (account_id,), per_account, "Das Kontingent deines Haushalts für Analysen und Bilder in 24 Stunden ist erreicht."),
        ("1=1", (), server_limit, "Das Kontingent des Servers für Analysen und Bilder in 24 Stunden ist erreicht."),
    ):
        count, oldest = c.execute(f"SELECT COUNT(*),MIN(created_at) FROM import_budget_usage WHERE {where}", params).fetchone()
        if count >= limit:
            retry_after = max(1, math.ceil((oldest or now)+WINDOW_SECONDS-now))
            raise HTTPException(429, message+" Bitte später erneut versuchen.", headers={"Retry-After": str(retry_after)})
    c.execute("INSERT INTO import_budget_usage(account_id,created_at) VALUES(?,?)", (account_id, now))
