"""API für Verzeichnis-Browser (lokal + rclone)."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import require_auth
from ..config_store import get_config

router = APIRouter(prefix="/api/browse", tags=["browse"], dependencies=[Depends(require_auth)])


# Whitelist: nur diese Roots + alles darunter ist erlaubt.
# /mnt ist der NAS-/Backup-Mountpoint; restliche stammen aus der Config.
_BASE_ALLOWED = ("/mnt", "/opt/scrapper/data", "/opt/scrapper/logs", "/opt/scrapper/temp")


def _allowed_roots() -> List[Path]:
    """Erlaubte Roots aus Base + Config-Pfaden zusammenstellen."""
    cfg = get_config()
    roots = set(_BASE_ALLOWED)
    paths = cfg.get("paths", default={}) or {}
    for key in ("recipe_dir", "wedding_dir", "temp_dir", "logs_dir"):
        v = paths.get(key) if isinstance(paths, dict) else None
        if isinstance(v, str) and v:
            roots.add(v)
    return [Path(r).resolve() for r in roots]


def _safe_path(path: str, *, must_be_under_allowed: bool = True) -> Path:
    if not path:
        raise HTTPException(400, "Pfad fehlt")
    p = Path(path).expanduser().resolve()
    if not must_be_under_allowed:
        return p
    allowed = _allowed_roots()
    for root in allowed:
        try:
            p.relative_to(root)
            return p
        except ValueError:
            continue
        except OSError:
            continue
    raise HTTPException(
        403,
        f"Zugriff auf {p} nicht erlaubt. Erlaubte Roots: " +
        ", ".join(str(r) for r in allowed),
    )


@router.get("/local")
def browse_local(path: str = "", show_files: bool = False) -> Dict[str, Any]:
    """Listet Unterverzeichnisse (optional Files) eines lokalen Pfads.

    Ohne ``path``: gibt die erlaubten Roots zurück.
    """
    allowed = _allowed_roots()
    if not path:
        return {
            "path": "",
            "parent": None,
            "exists": True,
            "is_root": True,
            "entries": [
                {"name": str(r), "path": str(r), "is_dir": True} for r in allowed
            ],
            "suggested_roots": [str(r) for r in allowed],
        }

    p = _safe_path(path)
    if not p.exists():
        return {
            "path": str(p),
            "parent": str(p.parent),
            "exists": False,
            "entries": [],
            "suggested_roots": [str(r) for r in allowed],
        }
    if not p.is_dir():
        raise HTTPException(400, "Kein Verzeichnis")

    entries = []
    try:
        for child in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if child.name.startswith("."):
                continue
            is_dir = child.is_dir()
            if not show_files and not is_dir:
                continue
            try:
                stat = child.stat()
                entries.append({
                    "name": child.name,
                    "path": str(child),
                    "is_dir": is_dir,
                    "size": stat.st_size if not is_dir else None,
                })
            except (OSError, PermissionError):
                pass
    except PermissionError:
        raise HTTPException(403, f"Keine Leseberechtigung für {p}")

    # Parent nur zurückgeben, wenn er noch in einem erlaubten Root liegt
    parent_str = None
    try:
        _safe_path(str(p.parent))
        parent_str = str(p.parent)
    except HTTPException:
        parent_str = ""  # signalisiert Root-Liste

    return {
        "path": str(p),
        "parent": parent_str,
        "exists": True,
        "entries": entries,
        "suggested_roots": [str(r) for r in allowed],
        "writable": os.access(p, os.W_OK),
    }


@router.get("/rclone")
def browse_rclone(path: str = "") -> Dict[str, Any]:
    """Listet einen rclone-Pfad. Wenn path leer: alle Remotes."""
    try:
        if not path:
            # Liste alle Remotes
            r = subprocess.run(
                ["rclone", "listremotes"], capture_output=True, text=True, timeout=10,
            )
            if r.returncode != 0:
                raise HTTPException(500, f"rclone listremotes: {r.stderr.strip()}")
            remotes = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
            return {
                "path": "",
                "parent": None,
                "is_root": True,
                "entries": [
                    {"name": rmt.rstrip(":"), "path": rmt, "is_dir": True, "is_remote": True}
                    for rmt in remotes
                ],
            }

        # Pfad validieren
        if path.startswith("-"):
            raise HTTPException(400, "rclone-Pfad darf nicht mit '-' beginnen")
        if any(c in path for c in ("\n", "\r", "\x00")):
            raise HTTPException(400, "rclone-Pfad enthält ungültige Zeichen")
        if ":" not in path:
            raise HTTPException(400, "rclone-Pfad muss 'remote:pfad' Format haben")

        # lsjson für strukturierte Daten ("--" trennt Optionen vom Argument)
        r = subprocess.run(
            ["rclone", "lsjson", "--dirs-only", "--", path],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0:
            raise HTTPException(500, f"rclone lsjson: {r.stderr.strip()[:200]}")

        import json
        items = json.loads(r.stdout or "[]")
        entries = []
        for it in sorted(items, key=lambda x: x.get("Name", "").lower()):
            entries.append({
                "name": it.get("Name"),
                "path": path.rstrip("/") + "/" + it.get("Name"),
                "is_dir": True,
                "is_remote": True,
                "size": it.get("Size"),
            })

        # Parent berechnen
        parent = None
        if path.endswith(":") or path.endswith(":/"):
            parent = ""   # zurück zu Remote-Liste
        else:
            # Eine Ebene hoch
            base, rest = path.split(":", 1)
            rest = rest.rstrip("/")
            if "/" in rest:
                parent = base + ":" + rest.rsplit("/", 1)[0]
            else:
                parent = base + ":"

        return {
            "path": path,
            "parent": parent,
            "is_root": False,
            "entries": entries,
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "rclone Timeout")
    except FileNotFoundError:
        raise HTTPException(500, "rclone nicht installiert")


@router.post("/local/mkdir")
def make_local_dir(payload: Dict[str, str]) -> Dict[str, Any]:
    """Erstellt einen neuen Unterordner."""
    path = payload.get("path")
    if not path:
        raise HTTPException(400, "path fehlt")
    p = _safe_path(path)
    try:
        p.mkdir(parents=True, exist_ok=True)
        return {"ok": True, "path": str(p)}
    except Exception as e:
        raise HTTPException(500, str(e))
