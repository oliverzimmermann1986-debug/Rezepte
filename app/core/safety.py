"""Datenschutz-/Härtungs-Helfer.

Ziele:
- nie halbfertige Bibliotheksordner veröffentlichen
- Text/JSON/Bytes atomar schreiben
- gelöschte/übersprungene Dateien in Quarantäne verschieben statt löschen
- Manifest + Checksummen für spätere Repair-/Audit-Läufe erzeugen
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def _resolve_under(path: Path, roots: Iterable[Path], *, kind: str) -> Path:
    """Löst einen Pfad ohne Symlink-Ausbruch unter allen ``roots`` auf.

    Sowohl der logische als auch der aufgelöste Pfad müssen innerhalb der
    erlaubten Wurzeln liegen. Symlinks in den unterhalb der Wurzel liegenden
    Komponenten werden abgelehnt, auch wenn ihr Ziel zufällig wieder innerhalb
    der Wurzel läge. Die Wurzel selbst darf ein administrativ konfigurierter
    Mount-/Symlink-Pfad sein.
    """
    logical = Path(os.path.abspath(str(path)))
    resolved = path.resolve(strict=True)
    for root in roots:
        logical_root = Path(os.path.abspath(str(root)))
        resolved_root = root.resolve(strict=True)
        try:
            logical.relative_to(logical_root)
            resolved.relative_to(resolved_root)
        except ValueError as exc:
            raise ValueError(f"Pfad liegt außerhalb der erlaubten Wurzel: {path}") from exc

        current = logical
        while current != logical_root:
            if current.is_symlink():
                raise ValueError(f"Symlink ist für Medienpfade nicht erlaubt: {current}")
            parent = current.parent
            if parent == current:
                raise ValueError(f"Pfadwurzel konnte nicht validiert werden: {path}")
            current = parent

    if kind == "file" and not resolved.is_file():
        raise ValueError(f"Keine reguläre Datei: {path}")
    if kind == "directory" and not resolved.is_dir():
        raise ValueError(f"Kein Verzeichnis: {path}")
    return resolved


def resolve_regular_file_under(path: Path, *roots: Path) -> Path:
    return _resolve_under(path, roots, kind="file")


def resolve_directory_under(path: Path, *roots: Path) -> Path:
    return _resolve_under(path, roots, kind="directory")


def fsync_dir(path: Path) -> None:
    """Best-effort fsync eines Verzeichnisses, damit Renames auch nach Stromausfall
    stabiler sind. Auf manchen FS/OS nicht unterstützt -> ignorieren."""
    try:
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except Exception:
        pass


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    fsync_dir(path.parent)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    _atomic_write(path, data or b"")


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    _atomic_write(path, (text or "").encode(encoding))


def atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
    atomic_write_text(path, text + "\n")


def safe_unique_path(path: Path) -> Path:
    """Liefert einen freien Pfad. Vermeidet Überschreiben auch bei Race-Bedingungen
    soweit möglich durch Zeitstempel+Random-Suffix."""
    if not path.exists():
        return path
    stamp = time.strftime("%Y%m%d_%H%M%S")
    for i in range(1, 1000):
        candidate = path.parent / f"{path.name}_{stamp}_{i:03d}"
        if not candidate.exists():
            return candidate
    return path.parent / f"{path.name}_{stamp}_{uuid.uuid4().hex[:8]}"


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(target_dir: Path, *, source: Dict[str, Any] | None = None) -> Dict[str, Any]:
    files: List[Dict[str, Any]] = []
    for child in sorted(target_dir.rglob("*")):
        if not child.is_file() or child.name == ".scrapper-manifest.json":
            continue
        rel = child.relative_to(target_dir).as_posix()
        try:
            files.append({
                "path": rel,
                "size": child.stat().st_size,
                "sha256": sha256_file(child),
            })
        except Exception as e:
            files.append({"path": rel, "error": str(e)})
    return {
        "version": 1,
        "created_at": time.time(),
        "source": source or {},
        "file_count": len([f for f in files if not f.get("error")]),
        "files": files,
    }


def write_manifest(target_dir: Path, *, source: Dict[str, Any] | None = None) -> Dict[str, Any]:
    manifest = build_manifest(target_dir, source=source)
    atomic_write_json(target_dir / ".scrapper-manifest.json", manifest)
    return manifest


def verify_manifest(target_dir: Path) -> Dict[str, Any]:
    manifest_path = target_dir / ".scrapper-manifest.json"
    if not manifest_path.exists():
        return {"ok": False, "error": "manifest_missing", "target_dir": str(target_dir)}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "error": f"manifest_unreadable: {e}", "target_dir": str(target_dir)}

    missing = []
    changed = []
    for item in manifest.get("files") or []:
        rel = item.get("path")
        if not rel:
            continue
        p = target_dir / rel
        if not p.exists():
            missing.append(rel)
            continue
        try:
            size = p.stat().st_size
            digest = sha256_file(p)
            if size != item.get("size") or digest != item.get("sha256"):
                changed.append(rel)
        except Exception as e:
            changed.append(f"{rel}: {e}")
    return {
        "ok": not missing and not changed,
        "target_dir": str(target_dir),
        "missing": missing,
        "changed": changed,
        "manifest": manifest,
    }


class AtomicDirectoryCommit:
    """Schreibt einen Bibliotheksordner zuerst nach .incoming und published ihn
    danach via os.replace/rename in den finalen Zielpfad.

    Wichtig: staging_dir liegt in target.parent, damit der Rename im gleichen
    Filesystem atomar ist.
    """

    def __init__(self, target_dir: Path):
        self.requested_target = target_dir
        self.target_dir = safe_unique_path(target_dir)
        self.stage_root = self.target_dir.parent / ".incoming"
        self.stage_dir = self.stage_root / f"{self.target_dir.name}.{os.getpid()}.{uuid.uuid4().hex[:10]}"
        self._committed = False

    def __enter__(self) -> "AtomicDirectoryCommit":
        self.stage_root.mkdir(parents=True, exist_ok=True)
        self.stage_dir.mkdir(parents=False, exist_ok=False)
        return self

    def path(self, *parts: str) -> Path:
        return self.stage_dir.joinpath(*parts)

    def commit(self, *, manifest_source: Dict[str, Any] | None = None) -> Path:
        write_manifest(self.stage_dir, source=manifest_source)
        # Saubere Durability für alle Dateien im Staging-Verzeichnis.
        for child in self.stage_dir.rglob("*"):
            if child.is_file():
                try:
                    with open(child, "rb") as f:
                        os.fsync(f.fileno())
                except Exception:
                    pass
        fsync_dir(self.stage_dir)
        self.stage_dir.replace(self.target_dir)
        fsync_dir(self.target_dir.parent)
        self._committed = True
        with contextlib.suppress(Exception):
            if self.stage_root.exists() and not any(self.stage_root.iterdir()):
                self.stage_root.rmdir()
        return self.target_dir

    def __exit__(self, exc_type, exc, tb) -> None:
        if not self._committed:
            shutil.rmtree(self.stage_dir, ignore_errors=True)
            with contextlib.suppress(Exception):
                if self.stage_root.exists() and not any(self.stage_root.iterdir()):
                    self.stage_root.rmdir()


def quarantine_move(path: Path, trash_root: Path, *, reason: str = "manual", source: Dict[str, Any] | None = None) -> Optional[Path]:
    """Verschiebt Datei/Ordner in Quarantäne statt sie zu löschen.

    Return: Zielpfad in Trash oder None falls Quelldatei nicht existiert.
    """
    if not path or not path.exists():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe_name = path.name.replace("/", "_")[:120]
    dest_dir = trash_root / stamp[:10] / f"{stamp}-{uuid.uuid4().hex[:8]}-{reason}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / safe_name
    moved = False
    try:
        # shutil.move behandelt Cross-Device-Moves selbst. Ein zusätzlicher
        # Copy/Delete-Fallback würde bei einem Teilfehler das Original
        # möglicherweise löschen, obwohl das Ziel unvollständig ist.
        shutil.move(str(path), str(dest))
        moved = True
        meta = {
            "reason": reason,
            "original_path": str(path),
            "quarantined_at": time.time(),
            "source": source or {},
        }
        atomic_write_json(dest_dir / "quarantine.json", meta)
        fsync_dir(dest_dir)
    except Exception:
        # Ohne Manifest ist das Objekt nicht zuverlässig auffindbar. Deshalb
        # den Move kompensieren, statt eine anonyme Quarantäne zurückzulassen.
        if moved and dest.exists() and not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dest), str(path))
        with contextlib.suppress(OSError):
            (dest_dir / "quarantine.json").unlink(missing_ok=True)
            dest_dir.rmdir()
        raise
    return dest


def list_quarantine(trash_root: Path, limit: int = 200) -> List[Dict[str, Any]]:
    if not trash_root.exists():
        return []
    items: List[Dict[str, Any]] = []
    for meta_path in sorted(trash_root.rglob("quarantine.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {"error": "quarantine.json unreadable"}
        payloads = [p for p in meta_path.parent.iterdir() if p.name != "quarantine.json"]
        size = 0
        for p in payloads:
            try:
                if p.is_dir():
                    size += sum(c.stat().st_size for c in p.rglob("*") if c.is_file())
                elif p.is_file():
                    size += p.stat().st_size
            except Exception:
                pass
        items.append({
            "quarantine_dir": str(meta_path.parent),
            "payloads": [str(p) for p in payloads],
            "size_bytes": size,
            **meta,
        })
        if len(items) >= limit:
            break
    return items


def purge_old_quarantine(trash_root: Path, older_than_days: int) -> Dict[str, Any]:
    if older_than_days <= 0:
        return {"ok": True, "purged": 0, "skipped": True}
    cutoff = time.time() - older_than_days * 86400
    purged = 0
    bytes_freed = 0
    if not trash_root.exists():
        return {"ok": True, "purged": 0, "bytes_freed": 0}
    for meta_path in list(trash_root.rglob("quarantine.json")):
        qdir = meta_path.parent
        try:
            ts = qdir.stat().st_mtime
            if ts >= cutoff:
                continue
            for p in qdir.rglob("*"):
                if p.is_file():
                    bytes_freed += p.stat().st_size
            shutil.rmtree(qdir, ignore_errors=True)
            purged += 1
        except Exception:
            continue
    # Leere Datumsordner entfernen
    for d in sorted([p for p in trash_root.glob("*") if p.is_dir()], reverse=True):
        with contextlib.suppress(Exception):
            if not any(d.iterdir()):
                d.rmdir()
    return {"ok": True, "purged": purged, "bytes_freed": bytes_freed}


def atomic_copy_file(src: Path, dest: Path) -> None:
    """Kopiert eine Datei crash-sicher über eine temporäre Datei im Zielordner."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / f".{dest.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    with open(src, "rb") as fin, open(tmp, "wb") as fout:
        shutil.copyfileobj(fin, fout, length=1024 * 1024)
        fout.flush()
        os.fsync(fout.fileno())
    try:
        shutil.copystat(src, tmp)
    except Exception:
        pass
    os.replace(tmp, dest)
    fsync_dir(dest.parent)
