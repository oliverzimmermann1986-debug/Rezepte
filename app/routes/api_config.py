"""API für Config-CRUD."""
from __future__ import annotations

from pathlib import Path
import threading
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..auth import hash_password, is_hashed, request_user, require_admin
from ..config_store import get_config
from ..core.webhook import normalize_server_base_url
from ..jobs.scraper import invalidate_scraper_job
from .api_einkauf import normalize_einkauf_base_url

router = APIRouter(prefix="/api/config", tags=["config"], dependencies=[Depends(require_admin)])
_CONFIG_UPDATE_LOCK = threading.RLock()


@router.get("")
def read_config() -> Dict[str, Any]:
    """Liefert die Config zurück. Passwörter werden maskiert."""
    cfg = get_config().all()
    return _mask(cfg)


@router.put("")
def update_config(payload: Dict[str, Any], request: Request):
    """Schreibt die komplette Config neu. Maskierte Felder werden zurückgemerged."""
    with _CONFIG_UPDATE_LOCK:
        return _update_config_locked(payload, request)


def _update_config_locked(payload: Dict[str, Any], request: Request):
    """Read/merge/write als eine kritische Sektion gegen Lost Updates."""
    store = get_config()
    current = store.all()
    authenticated_username = request_user(request)

    # Laufzeit-/Datenpfade definieren die Sicherheitsgrenzen für Browse,
    # Audit, PDF und Backups. Sie dürfen nicht über eine HTTP-Anfrage auf '/'
    # oder andere beliebige Wurzeln umgebogen werden. Bewusste Infrastruktur-
    # Änderungen erfolgen direkt in der serverseitigen config.yaml.
    _assert_server_managed_paths_unchanged(payload, current)

    _assert_server_managed_einkauf_url_unchanged(payload, current)
    _assert_server_managed_service_urls_unchanged(payload, current)
    _assert_mail_target_change_requires_password(payload, current)

    # Ein gespeicherter OpenAI-Key darf nicht still an eine neue Base-URL
    # gebunden werden. Sonst könnte ein Benutzer nur die URL ändern, die
    # Key-Maske stehen lassen und den geheimen Key über /api/test/openai an
    # seinen Host senden.
    current_base = str(_get(current, ("ai", "openai", "base_url")) or "").rstrip("/")
    incoming_base_value = _get(payload, ("ai", "openai", "base_url"))
    incoming_base = str(
        current_base if incoming_base_value is None else incoming_base_value or ""
    ).rstrip("/")
    current_key = _get(current, ("ai", "openai", "api_key"))
    incoming_key = _get(payload, ("ai", "openai", "api_key"))
    if (
        current_key
        and current_base != incoming_base
        and (not incoming_key or incoming_key == MASKED)
    ):
        raise HTTPException(
            400,
            "Bei Änderung der OpenAI Base-URL muss der API-Key neu eingegeben werden",
        )

    # PUT bleibt aus Kompatibilitätsgründen erhalten, verhält sich aber wie
    # ein rekursiver Patch. Mobile/ältere Clients senden oft nur eine Sektion;
    # nicht mitgesendete Secrets, Mail-Konten oder Pfade dürfen dabei nicht
    # verschwinden.
    merged = _unmask(_deep_merge(current, payload), current)
    # Web-Passwort, falls Klartext, immer bcrypt-hashen
    incoming_password = _get(payload, ("web", "password"))
    new_password_hash = None
    if (
        isinstance(incoming_password, str)
        and incoming_password
        and incoming_password != MASKED
        and not is_hashed(incoming_password)
    ):
        if len(incoming_password) < 8:
            raise HTTPException(400, "Passwort muss mindestens 8 Zeichen haben")
        new_password_hash = hash_password(incoming_password)
        _set(merged, ("web", "password"), new_password_hash)
        current_version = int(_get(current, ("web", "session_version")) or 0)
        _set(merged, ("web", "session_version"), current_version + 1)

    authenticated_user = None
    if new_password_hash and authenticated_username and authenticated_username != "local":
        from ..db import get_db
        authenticated_user = get_db().user_get_by_name(authenticated_username)
        if authenticated_user:
            # Nach der Multi-User-Migration ist ausschließlich die DB die
            # Auth-Quelle. Den ungenutzten Legacy-Hash in config.yaml nicht
            # parallel verändern; das vermeidet einen Cross-Store-Split.
            _set(merged, ("web", "password"), _get(current, ("web", "password")))
            _set(
                merged,
                ("web", "session_version"),
                int(_get(current, ("web", "session_version")) or 0),
            )
    store.replace(merged)
    store.save()
    if new_password_hash:
        if authenticated_user:
            from ..db import get_db
            try:
                get_db().user_set_password(int(authenticated_user["id"]), new_password_hash)
            except Exception:
                # Der Request darf nicht mit einer neuen Config und alten
                # DB-Credentials enden. Andere Felder dieses PUT werden bei
                # einem Passwortfehler deshalb ebenfalls zurückgerollt.
                store.replace(current)
                store.save()
                raise
        initial_password = Path(
            _get(merged, ("paths", "data_dir")) or "/opt/scrapper/data"
        ) / ".initial-password"
        try:
            initial_password.unlink(missing_ok=True)
        except OSError:
            pass
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
    ("web", "share_token"),
    ("mail", "recipe", "password"),
    ("mail", "wedding", "password"),
    ("ai", "openai", "api_key"),
    ("einkauf", "app_token"),
    ("einkauf", "cf_access_client_secret"),
]
SERVER_MANAGED_PATH_KEYS = frozenset(
    {"data_dir", "db_path", "recipe_dir", "wedding_dir", "temp_dir", "logs_dir"}
)
SERVER_MANAGED_SERVICE_URL_PATHS = (
    ("ai", "openai", "base_url"),
    ("external_hdd", "shelly_url"),
)


def _assert_mail_target_change_requires_password(incoming: dict, current: dict) -> None:
    """Verhindert Wiederverwendung eines maskierten Secrets an einem neuen IMAP-Ziel."""
    for account in ("recipe", "wedding"):
        current_password = _get(current, ("mail", account, "password"))
        if not current_password:
            continue
        target_changed = any(
            _get(incoming, ("mail", account, key)) is not None
            and _get(incoming, ("mail", account, key))
            != _get(current, ("mail", account, key))
            for key in ("imap_host", "imap_port")
        )
        incoming_password = _get(incoming, ("mail", account, "password"))
        if target_changed and (not incoming_password or incoming_password == MASKED):
            raise HTTPException(
                400,
                f"Bei Änderung von mail.{account}.imap_host/imap_port muss "
                "das Passwort neu eingegeben werden",
            )


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


def _incoming_path_value(incoming: dict, path: tuple[str, ...]) -> tuple[bool, Any]:
    cur: Any = incoming
    for index, key in enumerate(path):
        if not isinstance(cur, dict):
            parent = ".".join(path[:index]) or "Konfiguration"
            raise HTTPException(400, f"{parent} muss ein Objekt sein")
        if key not in cur:
            return False, None
        cur = cur[key]
    return True, cur


def _normalized_path_value(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise HTTPException(400, "Konfigurationspfade müssen Textwerte sein")
    # Plattformneutral vergleichen: Der Server nutzt POSIX-Pfade, die Tests
    # laufen teilweise unter Windows. Auflösen gegen das lokale Dateisystem
    # würde hier falsche Gleich-/Ungleichheiten erzeugen.
    normalized = value.strip().replace("\\", "/")
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    return normalized.rstrip("/") or ("/" if normalized.startswith("/") else "")


def _assert_server_managed_paths_unchanged(incoming: dict, current: dict) -> None:
    paths = incoming.get("paths")
    if paths is None:
        return
    if not isinstance(paths, dict):
        raise HTTPException(400, "paths muss ein Objekt sein")
    current_paths = current.get("paths") or {}
    if not isinstance(current_paths, dict):
        current_paths = {}
    for key in SERVER_MANAGED_PATH_KEYS:
        if key not in paths:
            continue
        if _normalized_path_value(paths[key]) != _normalized_path_value(
            current_paths.get(key)
        ):
            raise HTTPException(
                400,
                f"paths.{key} ist serververwaltet und kann nicht per API geändert werden",
            )


def _normalized_optional_einkauf_url(value: Any) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        return ""
    if not isinstance(value, str):
        raise HTTPException(400, "einkauf.api_url muss ein Textwert sein")
    return normalize_einkauf_base_url(value)


def _assert_server_managed_einkauf_url_unchanged(
    incoming: dict,
    current: dict,
) -> None:
    """Die Infrastruktur-URL wird ausschließlich auf dem Server gesetzt.

    Vollständige Legacy-Formulare dürfen den vorhandenen Wert unverändert
    zurücksenden. So blockiert eine bestehende interne Loopback-URL das
    Speichern anderer Einstellungen nicht.
    """
    present, incoming_value = _incoming_path_value(
        incoming,
        ("einkauf", "api_url"),
    )
    if not present:
        return
    current_value = _get(current, ("einkauf", "api_url"))
    if _normalized_optional_einkauf_url(incoming_value) != _normalized_optional_einkauf_url(
        current_value
    ):
        raise HTTPException(
            400,
            "einkauf.api_url ist serververwaltet und kann nicht per API geändert werden",
        )


def _normalized_optional_server_url(value: Any, dotted_path: str) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        return ""
    if not isinstance(value, str):
        raise HTTPException(400, f"{dotted_path} muss ein Textwert sein")
    try:
        return normalize_server_base_url(value)
    except ValueError as exc:
        raise HTTPException(400, f"Ungültige serververwaltete URL {dotted_path}: {exc}") from exc


def _assert_server_managed_service_urls_unchanged(
    incoming: dict,
    current: dict,
) -> None:
    for path in SERVER_MANAGED_SERVICE_URL_PATHS:
        present, incoming_value = _incoming_path_value(incoming, path)
        if not present:
            continue
        dotted_path = ".".join(path)
        current_value = _get(current, path)
        if _normalized_optional_server_url(
            incoming_value,
            dotted_path,
        ) != _normalized_optional_server_url(current_value, dotted_path):
            raise HTTPException(
                400,
                f"{dotted_path} ist serververwaltet und kann nicht per API geändert werden",
            )


def _deep_merge(current: dict, incoming: dict) -> dict:
    """Rekursiver Config-Patch; Listen und Skalare werden bewusst ersetzt."""
    import copy
    out = copy.deepcopy(current)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


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
