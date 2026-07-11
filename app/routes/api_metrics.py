"""Prometheus-kompatibler /metrics-Endpoint.

Ein paar nützliche Counter und Gauges, die du in Grafana/Prometheus
scrapen kannst. Keine externe Dependency - wir bauen das Text-Format
selbst, weil prometheus_client der einzige Grund wäre, eine weitere
Library reinzuziehen.

Format-Doku: https://prometheus.io/docs/instrumenting/exposition_formats/
"""
from __future__ import annotations

import time
from typing import List

from fastapi import APIRouter, Response

from ..db import get_db


router = APIRouter(tags=["metrics"])


def _line(name: str, value, *, help_text: str = "", mtype: str = "gauge", labels: dict = None) -> List[str]:
    """Baut # HELP, # TYPE und die Werte-Zeile zusammen."""
    out = []
    if help_text:
        out.append(f"# HELP {name} {help_text}")
    out.append(f"# TYPE {name} {mtype}")
    if labels:
        label_str = ",".join(f'{k}="{v}"' for k, v in labels.items())
        out.append(f"{name}{{{label_str}}} {value}")
    else:
        out.append(f"{name} {value}")
    return out


@router.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    """Prometheus-Exposition.

    Bewusst KEINE Auth-Pflicht (das ist Prometheus-Konvention für /metrics).
    Wenn du das öffentlich exposed haben willst, blocke es im Reverse-Proxy
    oder lass den Endpoint hinter Cloudflare-Access für /metrics frei.
    """
    db = get_db()
    lines: List[str] = []
    now = time.time()

    # Pending-Counts
    with db.conn() as c:
        pending_total = c.execute(
            "SELECT COUNT(*) FROM pending WHERE status='pending'"
        ).fetchone()[0] or 0
        skipped_total = c.execute(
            "SELECT COUNT(*) FROM pending WHERE status IN ('skipped','auto_skipped')"
        ).fetchone()[0] or 0
        resolved_total = c.execute(
            "SELECT COUNT(*) FROM pending WHERE status='resolved'"
        ).fetchone()[0] or 0

        # Ältester Pending-Eintrag (in Sekunden)
        row = c.execute(
            "SELECT MIN(created_at) FROM pending WHERE status='pending'"
        ).fetchone()
        oldest_pending = (now - float(row[0])) if (row and row[0]) else 0

        # Jobs - laufende
        running_rows = c.execute(
            "SELECT kind, COUNT(*) FROM jobs WHERE status='running' GROUP BY kind"
        ).fetchall()
        running_by_kind = {r[0]: r[1] for r in running_rows}

        # Jobs - 24h status breakdown
        last_24h = c.execute(
            "SELECT kind, status, COUNT(*) FROM jobs "
            "WHERE ended_at > ? GROUP BY kind, status",
            (now - 86400,),
        ).fetchall()

        # History (insgesamt)
        history_total = c.execute("SELECT COUNT(*) FROM history").fetchone()[0] or 0

        # Download-Failures
        failures_total = c.execute(
            "SELECT COUNT(*) FROM download_failures WHERE attempts >= 3"
        ).fetchone()[0] or 0

        # Letzter Scraper-Lauf
        last_scraper = c.execute(
            "SELECT started_at, ended_at FROM jobs "
            "WHERE kind='scraper' AND status='ok' ORDER BY ended_at DESC LIMIT 1"
        ).fetchone()
        last_scraper_age = (now - float(last_scraper[1])) if last_scraper else -1
        last_scraper_duration = (float(last_scraper[1]) - float(last_scraper[0])) if last_scraper else -1

    # --- Pending ---
    lines += _line("scrapper_pending_count", pending_total,
                   help_text="Items waiting for manual classification")
    lines += _line("scrapper_pending_oldest_seconds", round(oldest_pending, 1),
                   help_text="Age of the oldest pending item in seconds")
    lines += _line("scrapper_pending_skipped_total", skipped_total,
                   help_text="Items that were skipped (manual or auto)", mtype="counter")
    lines += _line("scrapper_pending_resolved_total", resolved_total,
                   help_text="Items that were resolved/assigned", mtype="counter")

    # --- Running Jobs ---
    lines.append("# HELP scrapper_jobs_running Currently running jobs by kind")
    lines.append("# TYPE scrapper_jobs_running gauge")
    for kind in ("scraper", "reanalyze"):
        lines.append(f'scrapper_jobs_running{{kind="{kind}"}} {running_by_kind.get(kind, 0)}')

    # --- Jobs last 24h ---
    lines.append("# HELP scrapper_jobs_24h_total Jobs finished in last 24h by kind+status")
    lines.append("# TYPE scrapper_jobs_24h_total counter")
    for (kind, status, count) in last_24h:
        lines.append(f'scrapper_jobs_24h_total{{kind="{kind}",status="{status}"}} {count}')

    # --- History ---
    lines += _line("scrapper_history_total", history_total,
                   help_text="Total items in history (auto + manual + skipped + failed)",
                   mtype="counter")

    # --- Download failures ---
    lines += _line("scrapper_download_failures_total", failures_total,
                   help_text="URLs that exhausted their download retries (>=3 attempts)",
                   mtype="counter")

    # --- Last scraper run ---
    lines += _line("scrapper_last_run_age_seconds", round(last_scraper_age, 1),
                   help_text="Seconds since last successful scraper run (-1 if never)")
    lines += _line("scrapper_last_run_duration_seconds", round(last_scraper_duration, 1),
                   help_text="Duration of last successful scraper run (-1 if never)")

    body = "\n".join(lines) + "\n"
    return Response(body, media_type="text/plain; version=0.0.4; charset=utf-8")
