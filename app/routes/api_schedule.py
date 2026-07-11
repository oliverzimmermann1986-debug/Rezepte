"""API für den systemd-Timer des Inhaltsimports."""
from __future__ import annotations

import json
import logging
import re
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import require_auth
from ..config_store import get_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/schedule", tags=["schedule"], dependencies=[Depends(require_auth)])

TIMER_FILES = {
    "scraper": "/etc/systemd/system/scrapper-job.timer",
}

# Erlaubt: systemd-OnCalendar-Zeichen (Buchstaben/Ziffern/: * / , . - Leerzeichen).
# Newlines/Quotes/Semikolons/Backslash explizit nicht.
_ONCALENDAR_RE = re.compile(r"^[A-Za-z0-9:*/,.\- ]{1,200}$")
_schedule_request_lock = threading.Lock()


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
    # Semantik-Check via systemd-analyze
    try:
        r = subprocess.run(
            ["/usr/bin/systemd-analyze", "calendar", v],
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
        # systemd-analyze fehlt: dann eben nur Regex-Check
        logger.warning("systemd-analyze nicht gefunden, semantische Prüfung übersprungen")
    return v


def _read_oncalendar(timer_path: str) -> Optional[str]:
    p = Path(timer_path)
    if not p.exists():
        return None
    for line in p.read_text().splitlines():
        m = re.match(r'\s*OnCalendar\s*=\s*(.*)', line)
        if m:
            return m.group(1).strip()
    return None


def _queue_schedule_request(new_value: str) -> Dict:
    """Queue a request for the root-owned systemd path unit atomically."""
    data_dir = Path(get_config().path).parent.resolve()
    request_path = data_dir / "scraper-schedule.request"
    result_path = data_dir / "scraper-schedule.result"
    tmp_path = data_dir / f".scraper-schedule.request.{os.getpid()}.tmp"

    with _schedule_request_lock:
        try:
            result_path.unlink(missing_ok=True)
            tmp_path.write_text(new_value + "\n", encoding="utf-8")
            tmp_path.chmod(0o600)
            tmp_path.replace(request_path)
        except OSError as exc:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise HTTPException(
                500,
                f"Zeitplan-Anforderung konnte nicht gespeichert werden: {exc}",
            ) from exc

        # Die root-eigene Path-Unit publiziert ein eindeutiges Resultat. So wird
        # ein systemctl-Fehler nicht als erfolgreicher Queue-Vorgang gemeldet.
        timer_path = TIMER_FILES["scraper"]
        for _ in range(700):
            if result_path.is_file():
                try:
                    result = json.loads(result_path.read_text(encoding="utf-8"))
                except (OSError, ValueError, TypeError):
                    result = None
                if isinstance(result, dict) and result.get("value") == new_value:
                    result_path.unlink(missing_ok=True)
                    if not result.get("ok"):
                        message = str(result.get("message") or "unbekannter Fehler")[:300]
                        raise HTTPException(
                            500,
                            f"Zeitplan konnte nicht angewendet werden: {message}",
                        )
                    if _read_oncalendar(timer_path) != new_value:
                        raise HTTPException(
                            500,
                            "Zeitplan-Helfer meldet Erfolg, Timer-Datei stimmt jedoch nicht überein",
                        )
                    return {"ok": True, "applied": True}
            time.sleep(0.05)

        if request_path.exists():
            request_path.unlink(missing_ok=True)
            raise HTTPException(
                504,
                "Zeitplan-Anforderung wurde von systemd nicht innerhalb von 35 Sekunden abgeholt",
            )
        raise HTTPException(
            500,
            "Zeitplan-Anforderung wurde verarbeitet, aber es liegt kein Ergebnis vor; systemd-Journal prüfen",
        )


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
                ["/usr/bin/systemctl", "list-timers", "--no-pager", "--no-legend", unit_name],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0 and r.stdout.strip():
                # Format: "Thu 2024-05-21 14:00:00 UTC 30min Wed ..."
                line = r.stdout.strip()
                parts = line.split()
                if len(parts) >= 3:
                    next_run = " ".join(parts[:3])
        except Exception:
            pass
        # Letzter erfolgreicher Lauf aus DB
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
                    if j.get("summary"):
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
    """Aktualisiert OnCalendar und lädt systemd-Timer neu."""
    changes = []
    helper_result = None
    if body.scraper:
        clean = _validate_oncalendar(body.scraper)
        helper_result = _queue_schedule_request(clean)
        changes.append(("scraper", clean))
    if not changes:
        return {"ok": True, "message": "Nichts zu ändern"}

    # Schedule auch in config speichern (für Persistenz/Anzeige)
    cfg = get_config()
    if body.scraper:
        cfg.set("schedule", "scraper_interval", body.scraper)
    cfg.save()

    return {"ok": True, "changes": dict(changes), "details": helper_result}


@router.post("/preview")
def preview_oncalendar(body: ScheduleUpdate) -> Dict:
    """Berechnet Vorschau-Termine ohne zu speichern. Nutzt systemd-analyze calendar."""
    results = {}
    for kind, value in [("scraper", body.scraper)]:
        if not value:
            continue
        # Vor-Validierung (Regex), damit kein gefährlicher Input an systemd-analyze geht.
        v = value.strip()
        if "\n" in v or "\r" in v or "\x00" in v or not _ONCALENDAR_RE.match(v):
            results[kind] = {"ok": False, "error": "Ungültige Zeichen im Ausdruck"}
            continue
        r = subprocess.run(
            ["/usr/bin/systemd-analyze", "calendar", "--iterations=5", v],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            results[kind] = {"ok": False, "error": r.stderr.strip() or r.stdout.strip()}
            continue
        # Parse out next iterations
        next_runs = []
        for line in r.stdout.splitlines():
            m = re.match(r'\s+Next elapse:\s+(.*)', line) or re.match(r'\s+Iter\.\s*#\d+:\s+(.*)', line)
            if m:
                next_runs.append(m.group(1).strip())
        results[kind] = {"ok": True, "next_runs": next_runs[:5], "raw": r.stdout.strip()}
    return results
