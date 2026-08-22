"""Referenzsichere Bereinigung kurzlebiger Importdateien."""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Iterable


def _resolved_under(path: Path, root: Path) -> Path | None:
    try:
        resolved = path.resolve()
        resolved.relative_to(root)
        return resolved
    except (OSError, ValueError):
        return None


def _entry_size(path: Path) -> int:
    if path.is_symlink():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file() and not child.is_symlink():
                total += child.stat().st_size
        except OSError:
            continue
    return total


def _remove_entry(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def cleanup_temp_files(
    temp_root: Path,
    active_paths: Iterable[str | Path],
    *,
    older_than_days: int = 7,
    pending_orphan_grace_hours: int = 1,
    now: float | None = None,
) -> dict:
    """Entfernt alte Arbeitsdateien und nicht mehr referenzierte Pending-Dateien.

    ``pending`` wird nicht pauschal gelöscht: Pfade aktiver DB-Einträge bleiben
    erhalten. Die kurze Karenz schützt den Moment zwischen Dateiablage und
    anschließendem DB-Insert vor einem parallelen Wartungslauf.
    """
    root = temp_root.resolve()
    result = {"ok": True, "removed": 0, "bytes_removed": 0, "errors": []}
    if not root.is_dir():
        return result

    active: set[Path] = set()
    for value in active_paths:
        if not value:
            continue
        resolved = _resolved_under(Path(value), root)
        if resolved is not None:
            active.add(resolved)

    current = time.time() if now is None else now
    old_cutoff = current - max(1, older_than_days) * 86400
    orphan_cutoff = current - max(1, pending_orphan_grace_hours) * 3600
    candidates: list[Path] = []

    for child in root.iterdir():
        try:
            resolved = _resolved_under(child, root)
            if resolved is None or resolved == root:
                continue
            if child.name != "pending":
                if child.lstat().st_mtime < old_cutoff:
                    candidates.append(child)
                continue
            if not child.is_dir() or child.is_symlink():
                if child.lstat().st_mtime < orphan_cutoff and resolved not in active:
                    candidates.append(child)
                continue
            for pending_file in child.rglob("*"):
                if pending_file.is_dir() and not pending_file.is_symlink():
                    continue
                pending_resolved = _resolved_under(pending_file, root)
                if pending_resolved is None or pending_resolved in active:
                    continue
                if pending_file.lstat().st_mtime < orphan_cutoff:
                    candidates.append(pending_file)
        except OSError as exc:
            result["errors"].append(f"{child.name}: {exc}")

    for candidate in candidates:
        try:
            size = _entry_size(candidate)
            _remove_entry(candidate)
            result["removed"] += 1
            result["bytes_removed"] += size
        except OSError as exc:
            result["errors"].append(f"{candidate.name}: {exc}")

    pending_dir = root / "pending"
    if pending_dir.is_dir():
        for directory in sorted(
            (item for item in pending_dir.rglob("*") if item.is_dir()),
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            try:
                directory.rmdir()
            except OSError:
                pass

    result["ok"] = not result["errors"]
    return result
