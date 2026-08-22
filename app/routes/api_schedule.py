"""API für die sichere Verwaltung des Scraper-Zeitplans."""
from __future__ import annotations

import logging
import re
import subprocess
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
    p = Path(timer_path)
    if not p.exists():
        return None
    for line in p.read_text().splitlines():
        m = re.match(r'\s*OnCalendar\s*=\s*(.*)', line)
        if m:
            return m.group(1).strip()
    return None


def _write_oncalendar(timer_path: str, new_value: str) -> None:
    p = Path(timer_path)
    if not p.exists():
        raise HTTPException(500, f"Timer-File {timer_path} fehlt")
    lines = p.read_text(encoding="utf-8").splitlines()
    out_lines = []
    replaced = False
    for line in lines:
        if re.match(r'\s*OnCalendar\s*=', line):
            out_lines.append(f"OnCalendar={new_value}")
            replaced = True
        else:
            out_lines.append(line)
    if not replaced:
        new_lines = []
        for line in out_lines:
            if line.startswith("[Install]"):
                new_lines.append(f"OnCalendar={new_value}")
            new_lines.append(line)
        out_lines = new_lines
    try:
        p.write_text("\n".join(out_lines) + "\n", encoding="utf-8", newline="\n")
    except OSError as exc:
        # Häufigster Fall: der Dienst läuft mit systemd-Sandboxing (ProtectSystem),
        # das /etc read-only macht → EROFS. Klarer Hinweis statt nacktem 500.
        raise HTTPException(
            500,
            f"Timer-Datei nicht schreibbar ({exc.strerror}): {timer_path}. "
            "Dem Service scrapper-web Schreibrecht geben, z.B. drop-in mit "
            f"ReadWritePaths={timer_path}, dann daemon-reload + restart.",
        ) from exc


def _systemctl(*args) -> Dict:
    """Ruft systemctl auf - OHNE sudo. Der Dienst läuft gehärtet mit erzwungenem
    NoNewPrivileges (durch ProtectKernel*/SystemCallFilter), das sudo grundsätzlich
    blockiert. Stattdessen direkt via D-Bus/polkit; das braucht kein setuid.
    Erfordert eine polkit-Regel, die dem Service-User reload-daemon + manage-units
    für scrapper-job.{timer,service} erlaubt
    (siehe /etc/polkit-1/rules.d/49-scrapper-systemctl.rules)."""
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
    """Aktualisiert OnCalendar und lädt systemd-Timer neu."""
    changes = []
    originals: dict[str, str] = {}
    if body.scraper:
        clean = _validate_oncalendar(body.scraper)
        timer_path = TIMER_FILES["scraper"]
        try:
            originals[timer_path] = Path(timer_path).read_text(encoding="utf-8")
        except OSError as exc:
            raise HTTPException(500, f"Timer-Datei nicht lesbar: {timer_path}") from exc
        _write_oncalendar(timer_path, clean)
        changes.append(("scraper", clean))

    if not changes:
        return {"ok": True, "message": "Nichts zu ändern"}

    results = []

    def rollback() -> None:
        """Stellt Timer und effektiven systemd-Zustand bestmöglich wieder her."""
        restored_units = []
        for timer_path, content in originals.items():
            try:
                Path(timer_path).write_text(content, encoding="utf-8", newline="\n")
                restored_units.append(Path(timer_path).name)
                results.append({"step": f"rollback {Path(timer_path).name}", "ok": True})
            except OSError as exc:
                logger.exception("Timer-Rollback fehlgeschlagen: %s", timer_path)
                results.append({
                    "step": f"rollback {Path(timer_path).name}",
                    "ok": False,
                    "stderr": str(exc),
                })
        if restored_units:
            results.append({"step": "rollback daemon-reload", **_systemctl("daemon-reload")})
            for unit in restored_units:
                results.append({"step": f"rollback restart {unit}", **_systemctl("restart", unit)})

    daemon = _systemctl("daemon-reload")
    results.append({"step": "daemon-reload", **daemon})
    if not daemon["ok"]:
        rollback()
        return {
            "ok": False,
            "error": "systemctl daemon-reload fehlgeschlagen - polkit-Regel für "
                     "User scrapper fehlt? (reload-daemon). Details: "
                     + (daemon.get("stderr") or "").strip()[:200],
            "details": results,
        }

    for kind, _ in changes:
        unit = Path(TIMER_FILES[kind]).name
        r = _systemctl("restart", unit)
        results.append({"step": f"restart {unit}", **r})
        if not r["ok"]:
            rollback()
            return {
                "ok": False,
                "error": f"{unit} konnte nicht neu gestartet werden; Änderung wurde zurückgerollt. "
                         + (r.get("stderr") or "")[:200],
                "details": results,
            }

    cfg = get_config()
    if body.scraper:
        cfg.set("schedule", "scraper_interval", body.scraper)
    cfg.save()

    return {"ok": True, "changes": dict(changes), "details": results}


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
