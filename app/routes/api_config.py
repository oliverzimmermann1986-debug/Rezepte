"""API für Config-CRUD."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import hash_password, is_hashed, require_auth
from ..config_store import get_config
from ..jobs.scraper import invalidate_scraper_job

router = APIRouter(prefix="/api/config", tags=["config"], dependencies=[Depends(require_auth)])


@router.get("")
def read_config() -> Dict[str, Any]:
    """Liefert die Config zurück. Passwörter werden maskiert."""
    cfg = get_config().all()
    return _mask(cfg)


@router.put("")
def update_config(payload: Dict[str, Any]):
    """Schreibt die komplette Config neu. Maskierte Felder werden zurückgemerged."""
    store = get_config()
    current = store.all()
    merged = _unmask(payload, current)
    # Web-Passwort, falls Klartext, immer bcrypt-hashen
    pw = _get(merged, ("web", "password"))
    if isinstance(pw, str) and pw and not is_hashed(pw):
        if len(pw) < 8:
            raise HTTPException(400, "Passwort muss mindestens 8 Zeichen haben")
        _set(merged, ("web", "password"), hash_password(pw))
    store.replace(merged)
    store.save()
    # ScraperJob hat 30+ Config-Werte gecached - invalidieren damit der
    # nächste Resolve/Reanalyze die neuen Settings nutzt.
    invalidate_scraper_job()
    return {"ok": True}


@router.post("/reload")
def reload_config():
    get_config().reload()
    return {"ok": True}


# -------------------- Helper --------------------
MASKED = "********"
MASK_PATHS = [
    ("web", "password"),
    ("web", "secret_key"),
    ("mail", "recipe", "password"),
    ("mail", "wedding", "password"),
]


def _get(d: dict, path: tuple):
    cur: Any = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def _set(d: dict, path: tuple, value: Any) -> None:
    cur = d
    for k in path[:-1]:
        if k not in cur or not isinstance(cur[k], dict):
            cur[k] = {}
        cur = cur[k]
    cur[path[-1]] = value


def _mask(cfg: dict) -> dict:
    import copy
    out = copy.deepcopy(cfg)
    for path in MASK_PATHS:
        v = _get(out, path)
        if v:
            _set(out, path, MASKED)
    # Webhooks sind eine Liste - URL maskieren weil Discord/Slack Tokens enthält
    if isinstance(out.get("webhooks"), list):
        for hook in out["webhooks"]:
            if isinstance(hook, dict) and hook.get("url"):
                hook["url"] = MASKED
    return out


def _unmask(incoming: dict, current: dict) -> dict:
    """Übernimmt aktuelle Werte wenn das Feld noch maskiert ist."""
    import copy
    out = copy.deepcopy(incoming)
    for path in MASK_PATHS:
        if _get(out, path) == MASKED:
            real = _get(current, path)
            if real is not None:
                _set(out, path, real)
    # Webhooks: pro Eintrag prüfen ob die URL noch maskiert ist - dann
    # aus aktueller Config rückübernehmen (Match per name)
    cur_hooks = {h.get("name"): h.get("url")
                 for h in (current.get("webhooks") or []) if isinstance(h, dict)}
    if isinstance(out.get("webhooks"), list):
        for hook in out["webhooks"]:
            if isinstance(hook, dict) and hook.get("url") == MASKED:
                hook["url"] = cur_hooks.get(hook.get("name"), "")
    return out


# ---------------- rclone Filter-Datei ----------------
# Get/Put für eine separate Filter-Datei (rclone --filter-from). Liegt
# außerhalb von config.yaml weil rclone das Format selbst parst und
# nicht über YAML gehen sollte.

class FilterPayload(BaseModel):
    content: str


def _filter_path() -> Path:
    """Pfad aus config holen mit fallback. Konstrain auf data/ damit
    User nicht /etc/passwd überschreibt."""
    cfg = get_config()
    p = cfg.get("backup", "filter_file", default="/opt/scrapper/data/rclone-filters.txt") \
        or "/opt/scrapper/data/rclone-filters.txt"
    # Constraint: Pfad muss innerhalb /opt/scrapper/data liegen
    resolved = Path(p).resolve()
    base = Path("/opt/scrapper/data").resolve()
    if not str(resolved).startswith(str(base)):
        raise HTTPException(400, f"filter_file muss unter /opt/scrapper/data liegen, ist: {resolved}")
    return resolved


@router.get("/filter-file")
def get_filter_file() -> dict:
    path = _filter_path()
    if not path.exists():
        return {"path": str(path), "exists": False, "content": ""}
    try:
        return {"path": str(path), "exists": True, "content": path.read_text(encoding="utf-8")}
    except Exception as e:
        raise HTTPException(500, f"Lesefehler: {e}")


@router.put("/filter-file")
def save_filter_file(body: FilterPayload) -> dict:
    path = _filter_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(body.content, encoding="utf-8")
        return {"ok": True, "path": str(path), "bytes": len(body.content.encode("utf-8"))}
    except Exception as e:
        raise HTTPException(500, f"Schreibfehler: {e}")


# ---------------- Log + Backup Management ----------------

@router.get("/logs/stats")
def logs_stats() -> dict:
    """Belegung des Logs-Verzeichnisses (Anzahl, Bytes, ältester File)."""
    cfg = get_config()
    logs_dir = Path(cfg.get("paths", "logs_dir", default="/opt/scrapper/logs"))
    if not logs_dir.exists():
        return {"path": str(logs_dir), "exists": False, "count": 0, "total_bytes": 0}
    count = 0
    total = 0
    oldest = None
    for p in logs_dir.rglob("*"):
        if not p.is_file():
            continue
        try:
            st = p.stat()
            count += 1
            total += st.st_size
            if oldest is None or st.st_mtime < oldest:
                oldest = st.st_mtime
        except OSError:
            pass
    return {
        "path": str(logs_dir), "exists": True,
        "count": count, "total_bytes": total,
        "oldest_age_days": round((__import__('time').time() - oldest) / 86400, 1) if oldest else 0,
        "retention_days": int(cfg.get("paths", "log_retention_days", default=30) or 30),
    }


@router.post("/logs/cleanup")
def logs_cleanup(days: int = None) -> dict:
    """Triggert Log-Cleanup synchron. Optional 'days'-Parameter überschreibt Config."""
    import subprocess as _sp
    import sys as _sys
    cmd = [_sys.executable, "-m", "app.cli", "log-cleanup"]
    if days is not None:
        cmd.append(str(days))
    try:
        r = _sp.run(cmd, capture_output=True, text=True, timeout=120, cwd="/opt/scrapper")
        return {"ok": r.returncode == 0, "stdout": r.stdout[-1500:],
                "stderr": r.stderr[-1500:] if r.stderr else ""}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/backups/list")
def backups_list() -> dict:
    """Listet alle vorhandenen DB-Backups gegliedert nach Tier."""
    cfg = get_config()
    data_dir = Path(cfg.get("paths", "data_dir", default="/opt/scrapper/data"))
    backups_root = data_dir / "backups"
    tiers: dict = {}
    if backups_root.exists():
        for tier in ("daily", "weekly", "monthly"):
            tier_dir = backups_root / tier
            if not tier_dir.exists():
                continue
            files = sorted(tier_dir.glob("scrapper-*.db*"),
                           key=lambda p: p.stat().st_mtime, reverse=True)
            tiers[tier] = [{
                "name": f.name, "path": str(f),
                "size_bytes": f.stat().st_size,
                "mtime": f.stat().st_mtime,
            } for f in files]
    return {"tiers": tiers, "backups_root": str(backups_root)}


@router.post("/backups/run-now")
def backups_run_now() -> dict:
    """Triggert ein DB-Backup synchron via CLI."""
    import subprocess as _sp
    import sys as _sys
    try:
        r = _sp.run(
            [_sys.executable, "-m", "app.cli", "db-backup"],
            capture_output=True, text=True, timeout=300, cwd="/opt/scrapper",
        )
        return {"ok": r.returncode == 0, "stdout": r.stdout[-2000:],
                "stderr": r.stderr[-2000:] if r.stderr else ""}
    except Exception as e:
        return {"ok": False, "error": str(e)}
