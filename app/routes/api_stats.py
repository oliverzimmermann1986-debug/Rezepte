"""Stats-Endpoint für Dashboard-Visualisierungen.

Liefert kompakte Daten für Frontend-Charts:
- Jobs pro Tag (letzte 30 Tage), aggregiert nach kind+status
- Confidence-Verteilung des KI-Cascades (Histogramm)
"""
from __future__ import annotations

import time
from typing import Dict, List

from fastapi import APIRouter, Depends

from ..auth import require_auth
from ..db import get_db

router = APIRouter(prefix="/api/stats", tags=["stats"], dependencies=[Depends(require_auth)])


@router.get("/jobs-per-day")
def jobs_per_day(days: int = 30) -> Dict:
    """Histogramm-Daten: Anzahl Jobs pro Tag der letzten N Tage.
    Returns: { 'days': [...], 'series': { kind: [counts...] } }
    """
    days = max(1, min(days, 365))
    now = time.time()
    cutoff = now - days * 86400

    db = get_db()
    with db.conn() as c:
        # Pro Tag + kind zählen
        rows = c.execute(
            "SELECT "
            "  strftime('%Y-%m-%d', started_at, 'unixepoch', 'localtime') AS day, "
            "  kind, "
            "  status, "
            "  COUNT(*) AS n "
            "FROM jobs "
            "WHERE started_at >= ? AND ended_at IS NOT NULL "
            "GROUP BY day, kind, status "
            "ORDER BY day ASC",
            (cutoff,),
        ).fetchall()

    # Day-Liste vorbereiten (lückenlos)
    from datetime import datetime, timedelta
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    day_list = [
        (today - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
        for i in range(days)
    ]
    day_idx = {d: i for i, d in enumerate(day_list)}

    # series[kind] = [count_day1, count_day2, ...]
    series: Dict[str, List[int]] = {}
    for r in rows:
        kind = r["kind"]
        day = r["day"]
        if day not in day_idx:
            continue
        if kind not in series:
            series[kind] = [0] * days
        series[kind][day_idx[day]] += r["n"]

    return {"days": day_list, "series": series}


@router.get("/confidence-histogram")
def confidence_histogram(buckets: int = 10) -> Dict:
    """Histogramm der KI-Confidence-Werte aller Pending-Items.
    Hilft den ``confidence_threshold`` rational zu setzen."""
    buckets = max(2, min(buckets, 20))
    db = get_db()
    with db.conn() as c:
        rows = c.execute(
            "SELECT CAST(json_extract(ai_suggestion, '$.confidence') AS REAL) AS conf "
            "FROM pending "
            "WHERE conf IS NOT NULL"
        ).fetchall()

    values = [r["conf"] for r in rows if r["conf"] is not None]
    if not values:
        return {"buckets": [], "counts": [], "total": 0}

    bucket_size = 1.0 / buckets
    counts = [0] * buckets
    for v in values:
        idx = min(int(v / bucket_size), buckets - 1)
        counts[idx] += 1

    bucket_labels = [
        f"{round(i * bucket_size, 1)}-{round((i+1) * bucket_size, 1)}"
        for i in range(buckets)
    ]
    return {"buckets": bucket_labels, "counts": counts, "total": len(values)}


@router.get("/per-pair")
def per_pair_stats(days: int = 30) -> Dict:
    """Pro Backup-Paar aggregierte Statistik über N Tage.

    Liest jobs.summary (JSON) und entpackt das 'pairs'-Array. Zählt
    Runs, OK/Fail, Bytes, Durchschnitts-Duration.

    Returns: {days, pairs: [{name, runs, ok, failed, avg_duration_sec,
                            total_transferred, last_run, last_status}]}
    """
    days = max(1, min(days, 365))
    cutoff = time.time() - days * 86400

    db = get_db()
    with db.conn() as c:
        rows = c.execute(
            "SELECT started_at, ended_at, status, summary FROM jobs "
            "WHERE kind IN ('backup','quicksync') AND ended_at >= ? "
            "ORDER BY started_at DESC",
            (cutoff,),
        ).fetchall()

    pair_stats: Dict[str, Dict] = {}
    import json
    for row in rows:
        try:
            summary = json.loads(row["summary"] or "{}")
        except Exception:
            continue
        pairs = summary.get("pairs") or []
        if not pairs:
            # Quick-Sync hat kein pairs[]; ad-hoc als (remote→local)-Name
            remote = summary.get("remote", "")
            local = summary.get("local", "")
            if remote and local:
                pairs = [{
                    "name": f"{remote} ⇄ {local}",
                    "ok": summary.get("ok") == True or row["status"] == "ok",
                    "transferred_bytes": summary.get("transferred_bytes", 0),
                }]
        for p in pairs:
            name = p.get("name", "?")
            if name not in pair_stats:
                pair_stats[name] = {
                    "name": name,
                    "runs": 0, "ok": 0, "failed": 0,
                    "total_transferred": 0,
                    "duration_sum": 0.0,
                    "last_run": None,
                    "last_status": None,
                }
            ps = pair_stats[name]
            ps["runs"] += 1
            if p.get("ok"):
                ps["ok"] += 1
            else:
                ps["failed"] += 1
            tb = p.get("transferred_bytes") or p.get("transferred") or 0
            try:
                ps["total_transferred"] += int(tb) if isinstance(tb, (int, float)) else 0
            except Exception:
                pass
            # Duration: aus job.summary oder von started_at/ended_at fallback
            d = summary.get("duration_sec")
            if d:
                ps["duration_sum"] += d / max(len(pairs), 1)
            # Last-run nur einmal setzen (Sort ist DESC = neueste zuerst)
            if ps["last_run"] is None:
                ps["last_run"] = row["started_at"]
                ps["last_status"] = "ok" if p.get("ok") else "failed"

    # Avg-Duration berechnen
    result = []
    for ps in pair_stats.values():
        runs = max(ps["runs"], 1)
        result.append({
            "name": ps["name"],
            "runs": ps["runs"],
            "ok": ps["ok"],
            "failed": ps["failed"],
            "success_rate": round(ps["ok"] / runs * 100, 1),
            "avg_duration_sec": round(ps["duration_sum"] / runs, 1),
            "total_transferred": ps["total_transferred"],
            "last_run": ps["last_run"],
            "last_status": ps["last_status"],
        })
    result.sort(key=lambda x: x["runs"], reverse=True)
    return {"days": days, "pairs": result}
