"""API für Verzeichnis-Browser (lokal + rclone)."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import require_auth

router = APIRouter(prefix="/api/browse", tags=["browse"], dependencies=[Depends(require_auth)])


# Verbotene Root-Pfade (Sicherheits-Whitelist)
FORBIDDEN_PREFIXES = ("/etc/", "/root/", "/boot/", "/sys/", "/proc/", "/dev/")
SUGGESTED_ROOTS = ["/mnt", "/opt/scrapper", "/home", "/srv", "/var/data"]


def _safe_path(path: str) -> Path:
    if not path:
        path = "/"
    p = Path(path).expanduser().resolve()
    sp = str(p)
    if any(sp == fp.rstrip("/") or sp.startswith(fp) for fp in FORBIDDEN_PREFIXES):
        raise HTTPException(403, f"Zugriff auf {p} nicht erlaubt")
    return p


@router.get("/local")
def browse_local(path: str = "/", show_files: bool = False) -> Dict[str, Any]:
    """Listet Unterverzeichnisse (optional Files) eines lokalen Pfads."""
    p = _safe_path(path)
    if not p.exists():
        # Nicht existierender Pfad → leerere Antwort mit can_create
        return {
            "path": str(p),
            "parent": str(p.parent),
            "exists": False,
            "entries": [],
            "suggested_roots": SUGGESTED_ROOTS,
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

    return {
        "path": str(p),
        "parent": str(p.parent) if str(p) != "/" else None,
        "exists": True,
        "entries": entries,
        "suggested_roots": SUGGESTED_ROOTS,
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

        # Konkreten Pfad listen
        # Format: "pcloud:/medien/Serien" oder "pcloud:"
        if ":" not in path:
            raise HTTPException(400, "rclone-Pfad muss 'remote:pfad' Format haben")

        # lsjson für strukturierte Daten
        r = subprocess.run(
            ["rclone", "lsjson", "--dirs-only", path],
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
