"""API für die sichere Verwaltung des Scraper-Zeitplans."""
from __future__ import annotations

import logging
import json
import re
import subprocess
from pathlib import Path
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import require_admin
from ..config_store import get_config
from ..core.safety import atomic_write_json
from ..db import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/schedule", tags=["schedule"], dependencies=[Depends(require_admin)])

TIMER_FILES = {
    "scraper": "/etc/systemd/system/scrapper-job.timer",
}

# Erlaubt: systemd-OnCalendar-Zeichen (Buchstaben/Ziffern/: * / , . - Leerzeichen).
_ONCALENDAR_RE = re.compile(r"^[A-Za-z0-9:*/,.\- ]{1,200}$")


def _validate_oncalendar(value: str) -> str:
    """Validiert + normalisiert. Wirft HTTPException bei ungültiger Eingabe."""
    if not isinstance(value, str):
        raise HTTPException(400, "OnCalendar muss String sein")
    v = value.strip()
    if not v:
        raise HTTPException(400, "OnCalendar darf nicht leer sein")
    if "\n" in v or "\r" in v or "\x00" in v:
        raise HTTPException(400, "OnCalendar darf keine Zeilenumbrüche enthalten")
    if not _ONCALENDAR_RE.match(v):
        raise HTTPException(
            400,
            "OnCalendar enthält ungültige Zeichen "
            "(erlaubt: A-Z a-z 0-9 : * / , . - Leerzeichen)",
        )
    try:
        r = subprocess.run(
            ["systemd-analyze", "calendar", v],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            raise HTTPException(
                400,
                f"OnCalendar ungültig: {(r.stderr or r.stdout).strip()[:200]}",
            )
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "systemd-analyze Timeout")
    except FileNotFoundError:
        logger.warning("systemd-analyze nicht gefunden, semantische Prüfung übersprungen")
    return v


def _read_oncalendar(timer_path: str) -> Optional[str]:
    base = Path(timer_path)
    override = base.parent / f"{base.name}.d" / "override.conf"
    p = override if override.is_file() else base
    if not p.exists():
        return None
    selected = None
    for line in p.read_text().splitlines():
        m = re.match(r'\s*OnCalendar\s*=\s*(.*)', line)
        if m:
            value = m.group(1).strip()
            if value:
                selected = value
    return selected


def _systemctl(*args) -> Dict:
    """Ruft systemctl ohne sudo auf; polkit erlaubt nur den Schedule-Helper."""
    cmd = ["systemctl"] + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return {
        "ok": r.returncode == 0,
        "stdout": r.stdout.strip(),
        "stderr": r.stderr.strip(),
        "cmd": " ".join(cmd),
    }


@router.get("")
def get_schedule() -> Dict:
    """Aktuelle OnCalendar-Werte + letzter & nächster Lauf."""
    from ..db import get_db
    db = get_db()
    result = {}
    for kind, path in TIMER_FILES.items():
        oc = _read_oncalendar(path)
        unit_name = Path(path).name
        next_run = None
        try:
            r = subprocess.run(
                ["systemctl", "list-timers", "--no-pager", "--no-legend", unit_name],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0 and r.stdout.strip():
                line = r.stdout.strip()
                parts = line.split()
                if len(parts) >= 3:
                    next_run = " ".join(parts[:3])
        except Exception:
            pass
        last_run = None
        last_summary = None
        try:
            jobs = db.job_list(kind=kind, limit=10)
            for j in jobs:
                if j.get("ended_at"):
                    last_run = j["ended_at"]
                    last_summary = {
                        "status": j.get("status"),
                        "duration": round(j["ended_at"] - j["started_at"]) if j.get("started_at") else None,
                    }
                    if j.get("summary") and kind == "scraper":
                        s = j["summary"]
                        last_summary["auto"] = s.get("auto", 0)
                        last_summary["pending"] = s.get("pending", 0)
                        last_summary["errors"] = s.get("errors", 0)
                    break
        except Exception:
            pass
        result[kind] = {
            "oncalendar": oc,
            "timer_file": path,
            "unit": unit_name,
            "next_run": next_run,
            "last_run": last_run,
            "last_summary": last_summary,
        }
    return result


class ScheduleUpdate(BaseModel):
    scraper: Optional[str] = None


@router.put("")
def update_schedule(body: ScheduleUpdate) -> Dict:
    """Übergibt einen validierten Wunsch an den root-eigenen Schedule-Helper."""
    if not body.scraper:
        return {"ok": True, "message": "Nichts zu ändern"}
    clean = _validate_oncalendar(body.scraper)
    data_dir = get_db().path.parent
    request_path = data_dir / "schedule-request.json"
    result_path = data_dir / "schedule-result.json"
    result_path.unlink(missing_ok=True)
    atomic_write_json(request_path, {"scraper": clean})
    applied = _systemctl("start", "scrapper-schedule-apply.service")
    if not applied["ok"]:
        request_path.unlink(missing_ok=True)
        raise HTTPException(
            503,
            "Schedule-Helper konnte nicht gestartet werden: "
            + (applied.get("stderr") or "unbekannter Fehler")[:200],
        )
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(503, "Schedule-Helper lieferte kein Ergebnis") from exc
    if not result.get("ok"):
        raise HTTPException(500, str(result.get("error") or "Schedule fehlgeschlagen"))
    cfg = get_config()
    cfg.set("schedule", "scraper_interval", clean)
    cfg.save()
    return {"ok": True, "changes": {"scraper": clean}}


@router.post("/preview")
def preview_oncalendar(body: ScheduleUpdate) -> Dict:
    """Berechnet Vorschau-Termine ohne zu speichern. Nutzt systemd-analyze calendar."""
    results = {}
    if not body.scraper:
        return results
    v = body.scraper.strip()
    if "\n" in v or "\r" in v or "\x00" in v or not _ONCALENDAR_RE.match(v):
        results["scraper"] = {"ok": False, "error": "Ungültige Zeichen im Ausdruck"}
        return results
    r = subprocess.run(
        ["systemd-analyze", "calendar", "--iterations=5", v],
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode != 0:
        results["scraper"] = {"ok": False, "error": r.stderr.strip() or r.stdout.strip()}
        return results
    next_runs = []
    for line in r.stdout.splitlines():
        m = re.match(r'\s+Next elapse:\s+(.*)', line) or re.match(r'\s+Iter\.\s*#\d+:\s+(.*)', line)
        if m:
            next_runs.append(m.group(1).strip())
    results["scraper"] = {"ok": True, "next_runs": next_runs[:5], "raw": r.stdout.strip()}
    return results
