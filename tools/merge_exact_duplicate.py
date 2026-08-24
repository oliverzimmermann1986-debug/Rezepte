"""Exakte Rezept-Dubletten mit Backup und Quarantaene zusammenfuehren.

Ohne ``--apply`` wird ausschliesslich geprueft. Der schreibende Lauf setzt
gleiche Kerndaten voraus, erzeugt einen SQLite-Backup sowie Rezeptversionen
und verwendet danach den bestehenden recoverable Soft-Delete/Merge-Pfad.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from app.db import Database, get_db
from app.recipes.manage import safe_merge_recipes


def _media_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ingredient_signature(db: Database, recipe_id: int) -> list[tuple[Any, ...]]:
    return [
        (
            row.get("name"),
            row.get("canonical_name"),
            row.get("amount"),
            row.get("unit"),
            row.get("raw"),
        )
        for row in db.recipe_ingredients_get(recipe_id)
    ]


def _verify_exact_pair(
    db: Database,
    *,
    keep_id: int,
    remove_id: int,
    archive_dir: Path | None,
    require_media_match: bool,
) -> dict[str, Any]:
    keep = db.recipe_get(keep_id)
    remove = db.recipe_get(remove_id)
    if not keep or not remove:
        raise ValueError("Mindestens eine Rezept-ID wurde nicht gefunden")
    if keep.get("deleted_at") is not None or remove.get("deleted_at") is not None:
        raise ValueError("Beide Rezepte muessen aktiv sein")
    if keep_id == remove_id:
        raise ValueError("keep-id und remove-id muessen verschieden sein")
    if (keep.get("name") or "").strip().casefold() != (
        remove.get("name") or ""
    ).strip().casefold():
        raise ValueError("Rezeptnamen sind nicht exakt gleich")
    if (keep.get("description") or "") != (remove.get("description") or ""):
        raise ValueError("Beschreibungen sind nicht exakt gleich")
    for field in ("type", "category", "servings"):
        if keep.get(field) != remove.get(field):
            raise ValueError(f"Metadaten unterscheiden sich: {field}")
    if _ingredient_signature(db, keep_id) != _ingredient_signature(db, remove_id):
        raise ValueError("Zutaten unterscheiden sich")

    keep_video: Path | None = None
    remove_video: Path | None = None
    media_equal: bool | None = None
    if archive_dir is not None:
        keep_video = archive_dir / f"{keep_id}.mp4"
        remove_video = archive_dir / f"{remove_id}.mp4"
        if keep_video.is_file() and remove_video.is_file():
            media_equal = _media_hash(keep_video) == _media_hash(remove_video)
        elif require_media_match:
            raise ValueError("Mindestens ein Archivvideo fehlt")
    if require_media_match and media_equal is not True:
        raise ValueError("Archivvideos sind nicht bitidentisch")

    keep_steps = db.recipe_steps_get(keep_id)
    remove_steps = db.recipe_steps_get(remove_id)
    return {
        "keep": keep,
        "remove": remove,
        "keep_steps": keep_steps,
        "remove_steps": remove_steps,
        "media_equal": media_equal,
        "keep_video": keep_video,
        "remove_video": remove_video,
    }


def _backup_database(db: Database, stamp: str) -> Path:
    backup_dir = db.path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"pre-duplicate-cleanup-{stamp}.db"
    source_connection = sqlite3.connect(str(db.path))
    destination_connection = sqlite3.connect(str(target))
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()
    return target


def merge_exact_duplicate(
    db: Database,
    *,
    keep_id: int,
    remove_id: int,
    archive_dir: Path | None,
    require_media_match: bool = False,
    apply: bool = False,
) -> dict[str, Any]:
    evidence = _verify_exact_pair(
        db,
        keep_id=keep_id,
        remove_id=remove_id,
        archive_dir=archive_dir,
        require_media_match=require_media_match,
    )
    result: dict[str, Any] = {
        "mode": "apply" if apply else "dry-run",
        "keep_id": keep_id,
        "remove_id": remove_id,
        "name": evidence["keep"].get("name"),
        "media_equal": evidence["media_equal"],
        "keep_steps": len(evidence["keep_steps"]),
        "remove_steps": len(evidence["remove_steps"]),
    }
    if not apply:
        return result

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = _backup_database(db, stamp)
    versions = {
        str(keep_id): db.recipe_version_create(
            keep_id,
            created_by="exact-duplicate-tool",
            source="duplicate-cleanup",
            reason=f"Vor Zusammenfuehrung von Rezept {remove_id}",
        ),
        str(remove_id): db.recipe_version_create(
            remove_id,
            created_by="exact-duplicate-tool",
            source="duplicate-cleanup",
            reason=f"Vor Zusammenfuehrung in Rezept {keep_id}",
        ),
    }

    keep_steps = evidence["keep_steps"]
    remove_steps = evidence["remove_steps"]
    copied_richer_steps = sum(
        len(step.get("instruction") or "") for step in remove_steps
    ) > sum(len(step.get("instruction") or "") for step in keep_steps)
    if copied_richer_steps:
        db.recipe_steps_set(keep_id, remove_steps)

    merge_result = safe_merge_recipes(
        db,
        source_id=remove_id,
        target_id=keep_id,
        delete_source=True,
    )

    archived_files: list[str] = []
    archive_quarantine: Path | None = None
    if archive_dir is not None and evidence["media_equal"] is True:
        archive_quarantine = archive_dir / ".duplicates-quarantine" / stamp
        for suffix in (".mp4", ".json"):
            source = archive_dir / f"{remove_id}{suffix}"
            if not source.exists():
                continue
            archive_quarantine.mkdir(parents=True, exist_ok=True)
            target = archive_quarantine / source.name
            if target.exists():
                raise RuntimeError(f"Quarantaeneziel existiert bereits: {target}")
            os.replace(source, target)
            archived_files.append(str(target))

    remaining = [
        int(recipe["id"])
        for recipe in db.recipe_list(limit=10_000)
        if (recipe.get("name") or "").strip().casefold()
        == (evidence["keep"].get("name") or "").strip().casefold()
    ]
    if remaining != [keep_id] or db.recipe_get(remove_id).get("deleted_at") is None:
        raise RuntimeError("Merge-Nachpruefung ist fehlgeschlagen")

    result.update(
        {
            "backup": str(backup),
            "versions": versions,
            "copied_richer_steps": copied_richer_steps,
            "merge": merge_result,
            "remaining_same_name_ids": remaining,
            "archive_quarantine": str(archive_quarantine) if archive_quarantine else None,
            "archived_files": archived_files,
        }
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep-id", type=int, required=True)
    parser.add_argument("--remove-id", type=int, required=True)
    parser.add_argument("--archive-dir", type=Path)
    parser.add_argument("--require-media-match", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = merge_exact_duplicate(
        get_db(),
        keep_id=args.keep_id,
        remove_id=args.remove_id,
        archive_dir=args.archive_dir,
        require_media_match=args.require_media_match,
        apply=args.apply,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
