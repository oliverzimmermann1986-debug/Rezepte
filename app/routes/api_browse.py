"""API für Verzeichnis-Browser (lokal)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import require_admin
from ..config_store import get_config

router = APIRouter(prefix="/api/browse", tags=["browse"], dependencies=[Depends(require_admin)])


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
