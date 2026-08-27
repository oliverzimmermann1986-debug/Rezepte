"""Rezept-Mutationen mit FS-Cleanup.

Die Audit-Dashboard-Aktionen (Rename, Delete, Merge) sind destruktiv und
brauchen sorgfältige Behandlung von:

  1. Path-Traversal: `new_name` von außen darf nicht aus dem recipe_dir
     ausbrechen können (kein `/`, `..`, `\\` durchlassen).
  2. FS-Konsistenz: beim Rename ändern sich Folder-Name + Datei-Namen
     (name.mp4, name.jpg) + info.json — alle synchron halten.
  3. DB-Konsistenz: Cascade räumt recipe_ingredients, recipe_steps,
     recipe_tags. shopping_cart.source_recipe_ids ist aber JSON-Liste,
     muss manuell gefiltert werden.
  4. Cross-Container: alle Operationen nur unterhalb von recipe_dir
     (Path.relative_to als Sanity-Check vor jedem FS-Aufruf).

Bewusst getrennt vom api_recipes-Router, damit die Logik auch von CLI-
Tools (z.B. Bulk-Cleanup-Scripts) wiederverwendet werden kann.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from ..config_store import get_config
from ..core.safety import (
    AtomicDirectoryCommit,
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_text,
)
from ..db import Database
from ..jobs.locks import file_lock_path_or_none
from .naming import normalize_recipe_name

logger = logging.getLogger(__name__)


@contextmanager
def _recipe_mutation_lock(db: Database, recipe_id: int) -> Iterator[None]:
    """Serialisiert DB/Filesystem-Mutationen auch über Prozesse hinweg."""
    lock_path = db.path.parent / f".{db.path.name}.recipe-{int(recipe_id)}.lock"
    with file_lock_path_or_none(lock_path, wait_seconds=5.0) as lock:
        if lock is None:
            raise RuntimeError(
                f"Rezept #{recipe_id} wird gerade in einem anderen Prozess geändert"
            )
        yield


# ────────────────────────────────────────────────────────────────────────
# Sanitization + Path-Sicherheit
# ────────────────────────────────────────────────────────────────────────

def sanitize_filename(name: str) -> str:
    """Identisch zu scraper._sanitize — duplikated to vermeiden circular
    imports. Wenn die scraper-Version sich ändert, hier mitziehen.

    Entfernt FS-unsichere Zeichen (Pfad-Separatoren, Newlines, Pipes etc.)
    und collapsed Whitespace zu Underscores. Bei leerem Resultat: 'Unbekannt'.
    """
    name = (name or "").strip()
    name = re.sub(r'[<>:"/\\|?*\n\r\t]', "", name)
    name = re.sub(r"\s+", "_", name)
    return name or "Unbekannt"


def _recipe_root() -> Path:
    """Wurzel-Pfad aus dem User-Config. Alle FS-Operationen müssen unterhalb
    davon stattfinden — sonst RuntimeError."""
    cfg = get_config()
    return Path(cfg.get("paths", "recipe_dir", default="/mnt/rezepte")).resolve()


def _assert_inside_root(path: Path) -> Path:
    """Path-Traversal-Schutz: path.resolve() muss unterhalb _recipe_root liegen.
    Sonst RuntimeError. Aufrufer können nicht durch geschickte ``../``-Werte
    aus dem Recipe-Verzeichnis ausbrechen."""
    root = _recipe_root()
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise RuntimeError(
            f"Pfad-Traversal-Schutz: {resolved} liegt nicht unter {root}. "
            f"Abbruch."
        )
    return resolved


# ────────────────────────────────────────────────────────────────────────
# Rename
# ────────────────────────────────────────────────────────────────────────

def safe_rename_recipe(
    db: Database,
    recipe_id: int,
    new_name: str,
    *,
    rename_folder: bool = True,
) -> Dict[str, Any]:
    """Benennt über den rollback-fähigen Metadatenpfad um."""
    new_name = normalize_recipe_name(new_name)
    if not new_name:
        raise ValueError("new_name darf nicht leer sein")
    if len(new_name) > 200:
        raise ValueError("new_name zu lang (max 200 Zeichen)")
    # Defensiver Pre-Check: Pfad-Separatoren im Display-Namen verbieten,
    # auch wenn _sanitize sie eh entfernen würde — sonst weiß der User
    # nicht warum sein "/" oder "\" weggebogen wurde.
    if any(ch in new_name for ch in ("/", "\\", "..")):
        raise ValueError(
            "new_name darf keine Pfad-Separatoren oder '..' enthalten"
        )

    recipe = db.recipe_get(recipe_id)
    if not recipe:
        raise ValueError(f"Recipe #{recipe_id} nicht gefunden")
    old_name = recipe.get("name") or ""
    old_folder = recipe.get("folder_path") or ""
    if not old_folder:
        raise ValueError(f"Recipe #{recipe_id} hat keinen folder_path")
    updated = safe_update_recipe_metadata(
        db,
        recipe_id,
        name=new_name,
        recipe_type=recipe.get("type") or "Sonstiges",
        category=recipe.get("category") or "Allgemein",
        description=recipe.get("description") or "",
        servings=recipe.get("servings"),
        url=recipe.get("url"),
        target_folder_override=old_folder if not rename_folder else None,
    )
    return {
        **updated,
        "old_name": old_name,
        "new_name": new_name,
        "old_folder": old_folder,
        "new_folder": updated.get("folder_path"),
    }


def safe_update_recipe_metadata(
    db: Database,
    recipe_id: int,
    **values: Any,
) -> Dict[str, Any]:
    with _recipe_mutation_lock(db, recipe_id):
        return _safe_update_recipe_metadata_locked(db, recipe_id, **values)


def safe_canonicalize_recipe_url(
    db: Database,
    recipe_id: int,
    *,
    expected_url: str,
    canonical_url: str,
) -> Dict[str, Any]:
    """Ersetzt einen Kurzlink konsistent in DB und ``info.json``.

    Mehrere TikTok-Kurzlinks können denselben Beitrag bezeichnen. Falls die
    Beitrags-URL bereits zu einem anderen aktiven Rezept gehört, bleibt das
    Kurzlink-Rezept unverändert und der bestehende kanonische Treffer wird
    zurückgegeben. Der Metadatenpfad hält DB, Sidecar und Ordner synchron und
    rollt bei einem Schreibfehler zurück.
    """
    canonical_url = str(canonical_url or "").strip()
    expected_url = str(expected_url or "").strip()
    if not canonical_url or not expected_url:
        raise ValueError("Quell- und Beitrags-URL dürfen nicht leer sein")

    with _recipe_mutation_lock(db, recipe_id):
        recipe = db.recipe_get(recipe_id)
        if not recipe or recipe.get("deleted_at") is not None:
            return {"ok": False, "error": "Rezept nicht gefunden"}
        if recipe.get("url") == canonical_url:
            return {
                "ok": True,
                "updated": False,
                "recipe_id": recipe_id,
                "folder_path": recipe.get("folder_path"),
            }
        if recipe.get("url") != expected_url:
            return {
                "ok": False,
                "error": "Die Rezeptquelle wurde zwischenzeitlich geändert",
            }

        conflict = db.recipe_get_by_url(canonical_url)
        if conflict and int(conflict["id"]) != recipe_id:
            return {
                "ok": True,
                "updated": False,
                "recipe_id": int(conflict["id"]),
                "folder_path": conflict.get("folder_path"),
                "conflict": True,
            }

        updated = _safe_update_recipe_metadata_locked(
            db,
            recipe_id,
            name=str(recipe.get("name") or "Unbekannt"),
            recipe_type=str(recipe.get("type") or "Sonstiges"),
            category=str(recipe.get("category") or "Allgemein"),
            description=str(recipe.get("description") or ""),
            servings=recipe.get("servings"),
            url=canonical_url,
            target_folder_override=recipe.get("folder_path"),
        )
        return {**updated, "updated": True}


def _safe_update_recipe_metadata_locked(
    db: Database,
    recipe_id: int,
    *,
    name: str,
    recipe_type: str,
    category: str,
    description: str,
    servings: Optional[int],
    url: Optional[str],
    target_folder_override: Optional[str] = None,
) -> Dict[str, Any]:
    """Aktualisiert sichtbare Metadaten konsistent in DB, Sidecars und Pfad.

    Typ/Kategorie/Name bilden die Ordnerstruktur. Deshalb wird der Rezeptordner
    bei Bedarf mit verschoben. Scheitert danach ein Sidecar- oder DB-Schritt,
    werden Dateinamen, Sidecars und Ordner bestmöglich auf den Ausgangsstand
    zurückgerollt.
    """
    values = {
        "name": normalize_recipe_name(name),
        "type": (recipe_type or "").strip(),
        "category": (category or "").strip(),
    }
    for label, value in values.items():
        if not value:
            raise ValueError(f"{label} darf nicht leer sein")
        if len(value) > 200:
            raise ValueError(f"{label} ist zu lang (maximal 200 Zeichen)")
        if any(part in value for part in ("/", "\\", "..")):
            raise ValueError(f"{label} darf keine Pfad-Separatoren oder '..' enthalten")
    if len(description or "") > 50_000:
        raise ValueError("description ist zu lang (maximal 50000 Zeichen)")
    if servings is not None and not 1 <= int(servings) <= 50:
        raise ValueError("servings muss zwischen 1 und 50 liegen")

    recipe = db.recipe_get(recipe_id)
    if not recipe:
        raise ValueError(f"Recipe #{recipe_id} nicht gefunden")

    old_folder_value = str(recipe.get("folder_path") or "").strip()
    old_folder = Path(old_folder_value)
    old_exists = bool(old_folder_value) and old_folder.exists()
    target_folder = old_folder
    created_parents: List[Path] = []
    moved = False
    file_renames: List[tuple[Path, Path]] = []
    original_info: Optional[bytes] = None
    original_description: Optional[bytes] = None
    info_existed = False
    description_existed = False

    if old_exists:
        old_folder = _assert_inside_root(old_folder)
        root = _recipe_root()
        target_folder = (
            Path(target_folder_override)
            if target_folder_override
            else root / sanitize_filename(values["type"]) / sanitize_filename(values["category"]) / sanitize_filename(values["name"])
        )
        target_folder = _assert_inside_root(target_folder)
        if target_folder.exists() and target_folder != old_folder:
            raise RuntimeError(f"Ziel-Folder existiert bereits: {target_folder}")
        info_path = old_folder / "info.json"
        desc_path = old_folder / "description.txt"
        info_existed = info_path.is_file()
        description_existed = desc_path.is_file()
        if info_existed:
            original_info = info_path.read_bytes()
            try:
                info = json.loads(original_info.decode("utf-8"))
            except Exception as exc:
                raise RuntimeError(f"info.json ist ungültig und wurde nicht überschrieben: {exc}") from exc
        else:
            info = {}
        if description_existed:
            original_description = desc_path.read_bytes()
    else:
        info = {}

    try:
        if old_exists and target_folder != old_folder:
            for parent in (target_folder.parent.parent, target_folder.parent):
                if not parent.exists():
                    parent.mkdir()
                    created_parents.append(parent)
            old_folder.rename(target_folder)
            moved = True

        working_folder = target_folder if old_exists else old_folder
        new_video_filename = recipe.get("video_filename")
        new_thumb_filename = recipe.get("thumb_filename")
        if old_exists:
            old_file_base = old_folder.name
            new_file_base = target_folder.name
            for source in list(working_folder.iterdir()):
                if not source.is_file() or source.stem != old_file_base:
                    continue
                destination = working_folder / f"{new_file_base}{source.suffix}"
                if destination == source:
                    continue
                if destination.exists():
                    raise RuntimeError(f"Zieldatei existiert bereits: {destination.name}")
                source.rename(destination)
                file_renames.append((source, destination))
                if recipe.get("video_filename") == source.name:
                    new_video_filename = destination.name
                if recipe.get("thumb_filename") == source.name:
                    new_thumb_filename = destination.name

            info.update({
                "name": values["name"],
                "type": values["type"],
                "category": values["category"],
                "description": description or "",
                "url": url,
            })
            atomic_write_json(working_folder / "info.json", info)
            if description:
                atomic_write_text(working_folder / "description.txt", description)
            else:
                (working_folder / "description.txt").unlink(missing_ok=True)

        with db.conn() as connection:
            connection.execute(
                "UPDATE recipes SET name=?, type=?, category=?, description=?, "
                "servings=?, url=?, folder_path=?, video_filename=?, thumb_filename=? "
                "WHERE id=?",
                (
                    values["name"], values["type"], values["category"],
                    description or None, servings, url,
                    str(target_folder) if old_exists else recipe.get("folder_path"),
                    new_video_filename, new_thumb_filename, recipe_id,
                ),
            )
    except Exception as exc:
        if old_exists:
            working_folder = target_folder if moved else old_folder
            try:
                info_path = working_folder / "info.json"
                if info_existed and original_info is not None:
                    atomic_write_bytes(info_path, original_info)
                else:
                    info_path.unlink(missing_ok=True)
                desc_path = working_folder / "description.txt"
                if description_existed and original_description is not None:
                    atomic_write_bytes(desc_path, original_description)
                else:
                    desc_path.unlink(missing_ok=True)
                for source, destination in reversed(file_renames):
                    if destination.exists() and not source.exists():
                        destination.rename(source)
                if moved and working_folder.exists() and not old_folder.exists():
                    working_folder.rename(old_folder)
                for parent in reversed(created_parents):
                    try:
                        parent.rmdir()
                    except OSError:
                        pass
            except Exception:
                logger.exception("Recipe #%s: Metadaten-Rollback unvollständig", recipe_id)
        if isinstance(exc, (ValueError, RuntimeError)):
            raise
        raise RuntimeError(f"Metadaten konnten nicht gespeichert werden: {exc}") from exc

    return {
        "ok": True,
        "recipe_id": recipe_id,
        "folder_path": str(target_folder) if old_exists else recipe.get("folder_path"),
        "moved": moved,
    }


def safe_duplicate_recipe(
    db: Database,
    recipe_id: int,
    *,
    new_name: str,
) -> Dict[str, Any]:
    """Erstellt eine eigenständige Rezeptvariante ohne Social-Media-Video.

    Der neue Ordner wird zuerst vollständig unter ``.incoming`` aufgebaut und
    erst danach atomar veröffentlicht. Text, Cover und Original-PDF werden
    übernommen; Videodateien und Quell-URL bewusst nicht. Schlägt der DB-Klon
    danach fehl, wird ausschließlich der gerade erzeugte Zielordner entfernt.
    """
    name = normalize_recipe_name(new_name)
    if not name:
        raise ValueError("Der Name der Variante darf nicht leer sein")
    if len(name) > 200:
        raise ValueError("Der Name der Variante ist zu lang (maximal 200 Zeichen)")
    if any(part in name for part in ("/", "\\", "..")):
        raise ValueError("Der Name darf keine Pfad-Separatoren oder '..' enthalten")

    recipe = db.recipe_get(recipe_id)
    if not recipe or recipe.get("deleted_at") is not None:
        raise ValueError(f"Recipe #{recipe_id} nicht gefunden")
    source_folder = _assert_inside_root(Path(str(recipe.get("folder_path") or "")))
    if not source_folder.is_dir():
        raise RuntimeError("Der Rezeptordner des Originals fehlt")

    recipe_type = str(recipe.get("type") or "Sonstiges").strip()
    category = str(recipe.get("category") or "Allgemein").strip()
    target = _assert_inside_root(
        _recipe_root()
        / sanitize_filename(recipe_type)
        / sanitize_filename(category)
        / sanitize_filename(name)
    )
    if target.exists():
        raise RuntimeError(f"Ziel-Folder existiert bereits: {target}")

    copied_names: set[str] = set()
    published: Optional[Path] = None
    new_id: Optional[int] = None
    allowed_suffixes = {
        ".jpg", ".jpeg", ".png", ".webp", ".heic",
        ".pdf", ".txt",
    }
    try:
        with AtomicDirectoryCommit(target) as transaction:
            for source in source_folder.iterdir():
                if (
                    not source.is_file()
                    or source.is_symlink()
                    or source.name == "info.json"
                    or source.suffix.lower() not in allowed_suffixes
                ):
                    continue
                shutil.copy2(source, transaction.path(source.name))
                copied_names.add(source.name)

            description = str(recipe.get("description") or "")
            if description:
                atomic_write_text(transaction.path("description.txt"), description)
                copied_names.add("description.txt")

            info: Dict[str, Any] = {}
            source_info = source_folder / "info.json"
            if source_info.is_file() and not source_info.is_symlink():
                try:
                    parsed = json.loads(source_info.read_text(encoding="utf-8"))
                    if isinstance(parsed, dict):
                        info = parsed
                except Exception:
                    logger.warning(
                        "Recipe #%s: ungültige info.json beim Varianten-Klon ignoriert",
                        recipe_id,
                    )
            info.update({
                "name": name,
                "type": recipe_type,
                "category": category,
                "description": description,
                "url": None,
                "variant_of": recipe_id,
                "created_at": time.time(),
            })
            info.pop("video_filename", None)
            atomic_write_json(transaction.path("info.json"), info)
            published = transaction.commit(manifest_source={
                "operation": "recipe_variant",
                "source_recipe_id": recipe_id,
            })

        thumb_filename = str(recipe.get("thumb_filename") or "")
        if thumb_filename not in copied_names:
            thumb_filename = None
        new_id = db.recipe_upsert(
            url=None,
            name=name,
            type=recipe_type,
            category=category,
            folder_path=str(published),
            description=recipe.get("description"),
            thumb_filename=thumb_filename,
            video_filename=None,
            source_added_at=time.time(),
        )
        db.recipe_clone_content(recipe_id, new_id)
    except Exception as exc:
        if new_id is not None:
            try:
                db.recipe_delete(new_id)
            except Exception:
                logger.exception("DB-Rollback für Varianten-Rezept #%s fehlgeschlagen", new_id)
        if published and published.exists():
            try:
                published = _assert_inside_root(published)
                shutil.rmtree(published)
            except Exception:
                logger.exception("Datei-Rollback für Rezeptvariante fehlgeschlagen: %s", published)
        if isinstance(exc, (ValueError, RuntimeError)):
            raise
        raise RuntimeError(f"Rezeptvariante konnte nicht erstellt werden: {exc}") from exc

    return {
        "ok": True,
        "recipe_id": new_id,
        "source_recipe_id": recipe_id,
        "name": name,
        "folder_path": str(published),
        "copied_files": len(copied_names),
        "video_copied": False,
    }


# ────────────────────────────────────────────────────────────────────────
# Delete
# ────────────────────────────────────────────────────────────────────────

def safe_delete_recipe(
    db: Database,
    recipe_id: int,
    *,
    delete_files: bool = False,
    hard: bool = False,
) -> Dict[str, Any]:
    with _recipe_mutation_lock(db, recipe_id):
        return _safe_delete_recipe_locked(
            db,
            recipe_id,
            delete_files=delete_files,
            hard=hard,
        )


def _safe_delete_recipe_locked(
    db: Database,
    recipe_id: int,
    *,
    delete_files: bool = False,
    hard: bool = False,
) -> Dict[str, Any]:
    """Löscht ein Rezept. Standardmäßig SOFT-DELETE (→ Papierkorb).

    - hard=False (Default): Soft-Delete — deleted_at=now, Rezept verschwindet
      aus Listings, taucht im Papierkorb auf. Wird nach 30 Tagen via
      Cleanup-Job endgültig entfernt. Restore möglich via recipe_restore.
    - hard=True: Endgültiges Löschen (HARD-DELETE) — DB-Zeile weg, cascade
      auf ingredients/steps/tags, optional auch FS-Folder. Verwendet vom
      Papierkorb-Endpunkt 'endgültig löschen' und vom Cleanup-Job.

    - Soft-Delete verschiebt einen vorhandenen Ordner immer in die
      wiederherstellbare Quarantäne, damit der Indexer ihn nicht sofort neu
      anlegt. Bei hard-delete steuert delete_files weiterhin, ob ein noch
      vorhandener Ordner mit entfernt werden darf.

    Cart wird bei BEIDEN Modi aufgeräumt (Cart-Einträge die auf das Rezept
    referenzieren werden upgedatet), damit Cart nicht auf 'unsichtbare'
    Rezepte zeigt.

    Returns: {ok, deleted_id, name, folder_deleted, cart_entries_updated, soft}
    """
    recipe = db.recipe_get(recipe_id)
    if not recipe:
        raise ValueError(f"Recipe #{recipe_id} nicht gefunden")
    if not hard and recipe.get("deleted_at") is not None:
        return {
            "ok": True,
            "deleted_id": recipe_id,
            "name": recipe.get("name"),
            "folder_deleted": bool(recipe.get("files_deleted")),
            "cart_entries_updated": 0,
            "soft": True,
            "already_deleted": True,
        }
    folder = recipe.get("deleted_folder_path") or recipe.get("folder_path")
    source_url = recipe.get("deleted_url") or recipe.get("url")
    name = recipe.get("name")

    previous_history = (
        db.deleted_history_latest(str(folder), reason="soft_delete")
        if hard and recipe.get("deleted_at") is not None and folder
        else None
    )
    previous_quarantine = Path(str(
        (previous_history or {}).get("quarantine_path") or ""
    )) if previous_history else None

    if hard and not delete_files and folder:
        candidate = Path(folder)
        if candidate.exists() or (previous_quarantine and previous_quarantine.exists()):
            folder_path = _assert_inside_root(candidate)
            raise RuntimeError(
                "Hard-Delete ohne Dateilöschung abgelehnt: "
                "der verbleibende Ordner würde beim nächsten Sync erneut indiziert"
            )

    # FS: nicht mehr hart löschen — in Quarantäne verschieben (Härtung gegen
    # Datenverlust). deleted_history bewahrt Herkunft für spätere Suche/Restore.
    folder_deleted = False
    qpath: Optional[Path] = None
    moved_folder: Optional[Path] = None
    # Auch ein normales Soft-Delete verschiebt vorhandene Dateien in die
    # wiederherstellbare Quarantäne. Würden sie im Rezeptbaum bleiben, würde
    # der nächste Indexlauf das gerade gelöschte Rezept sofort neu anlegen.
    if (delete_files or not hard) and folder:
        folder_path = Path(folder)
        if folder_path.exists():
            folder_path = _assert_inside_root(folder_path)
            from ..core.safety import quarantine_move
            trash_root = Path(get_config().get("safety", "trash_dir",
                              default="/opt/scrapper/data/trash"))
            qpath = quarantine_move(folder_path, trash_root,
                                    reason="hard_delete" if hard else "soft_delete",
                                    source={"recipe_id": recipe_id, "name": name})
            moved_folder = folder_path
            folder_deleted = True
            logger.info(f"Recipe #{recipe_id}: folder → Quarantäne {qpath}")
        else:
            logger.info(f"Recipe #{recipe_id}: folder {folder_path} existierte nicht mehr")

    history_entry = None
    if qpath is not None:
        history_entry = {
            "url": source_url,
            "content_type": recipe.get("type"),
            "name": name,
            "target_dir": folder,
        }
    try:
        persisted = db.recipe_delete_with_history(
            recipe_id,
            hard=hard,
            files_deleted=folder_deleted,
            history_entry=history_entry,
            quarantine_path=str(qpath or ""),
            reason="hard_delete" if hard else "soft_delete",
        )
    except Exception:
        if qpath and moved_folder and qpath.exists() and not moved_folder.exists():
            try:
                moved_folder.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(qpath), str(moved_folder))
                (qpath.parent / "quarantine.json").unlink(missing_ok=True)
                qpath.parent.rmdir()
            except Exception:
                logger.exception(
                    "Delete-Kompensation für Rezept #%s fehlgeschlagen",
                    recipe_id,
                )
        raise

    cart_updates = int(persisted["cart_entries_updated"])
    if hard:
        # Ein bestehender Papierkorb-Payload oder der gerade erzeugte Payload
        # hat nach dem atomaren DB-Hard-Delete keinen Restore-Zweck mehr.
        purge_payload = qpath or (previous_quarantine if delete_files else None)
        if purge_payload and purge_payload.exists():
            trash_root = Path(get_config().get(
                "safety", "trash_dir", default="/opt/scrapper/data/trash"
            )).resolve()
            try:
                purge_payload.resolve(strict=True).relative_to(trash_root)
            except (OSError, ValueError) as exc:
                raise RuntimeError(
                    "Quarantäne-Payload liegt außerhalb des Papierkorbs"
                ) from exc
            shutil.rmtree(purge_payload.parent)
        logger.info(f"Recipe #{recipe_id} '{name}' HARD-DELETE")
    else:
        logger.info(f"Recipe #{recipe_id} '{name}' → Papierkorb (files_deleted={folder_deleted})")

    return {
        "ok": True,
        "deleted_id": recipe_id,
        "name": name,
        "folder_deleted": folder_deleted,
        "cart_entries_updated": cart_updates,
        "soft": not hard,
    }


def safe_restore_recipe(db: Database, recipe_id: int) -> Dict[str, Any]:
    with _recipe_mutation_lock(db, recipe_id):
        return _safe_restore_recipe_locked(db, recipe_id)


def _safe_restore_recipe_locked(db: Database, recipe_id: int) -> Dict[str, Any]:
    """Stellt DB-Eintrag und gegebenenfalls Quarantäneordner gemeinsam her."""
    recipe = db.recipe_get(recipe_id)
    if not recipe:
        raise ValueError(f"Recipe #{recipe_id} nicht gefunden")
    if recipe.get("deleted_at") is None:
        return db.recipe_restore(recipe_id)

    original_folder = recipe.get("deleted_folder_path") or recipe.get("folder_path")
    folder = _assert_inside_root(Path(original_folder))
    files_deleted = bool(recipe.get("files_deleted"))
    moved_from: Optional[Path] = None

    if files_deleted:
        if folder.is_dir():
            # Ein manueller Repair kann den Ordner bereits zurückgebracht haben.
            files_restored = True
        else:
            history = db.deleted_history_latest(
                str(original_folder),
                reason="soft_delete",
            )
            quarantine_value = str(
                (history or {}).get("quarantine_path") or ""
            ).strip()
            if not quarantine_value:
                raise RuntimeError("Kein Quarantänepfad für dieses Rezept gespeichert")
            quarantine = Path(quarantine_value)

            trash_root = Path(get_config().get(
                "safety",
                "trash_dir",
                default="/opt/scrapper/data/trash",
            )).resolve()
            try:
                quarantine_resolved = quarantine.resolve(strict=True)
                quarantine_resolved.relative_to(trash_root)
            except (OSError, ValueError) as exc:
                raise RuntimeError(
                    "Gespeicherter Quarantänepfad fehlt oder liegt außerhalb des Papierkorbs"
                ) from exc

            if folder.exists():
                raise RuntimeError(f"Zielordner existiert bereits: {folder}")
            folder.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(quarantine_resolved), str(folder))
                moved_from = quarantine_resolved
            except Exception as exc:
                raise RuntimeError(
                    f"Dateien konnten nicht aus der Quarantäne wiederhergestellt werden: {exc}"
                ) from exc
            files_restored = True
    else:
        if not folder.is_dir():
            raise RuntimeError(
                "Rezeptordner fehlt; Restore würde einen aktiven Eintrag ohne Dateien erzeugen"
            )
        files_restored = False

    try:
        result = db.recipe_restore(
            recipe_id,
            files_restored=files_restored,
        )
        if not result.get("ok"):
            raise RuntimeError(result.get("error") or "DB-Restore fehlgeschlagen")
    except Exception:
        # Best-effort-Kompensation: Wenn die DB-Aktivierung scheitert, den
        # gerade verschobenen Ordner zurück in die Quarantäne legen.
        if moved_from and folder.exists() and not moved_from.exists():
            try:
                moved_from.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(folder), str(moved_from))
            except Exception:
                logger.exception(
                    "Restore-Kompensation für Rezept #%s fehlgeschlagen",
                    recipe_id,
                )
        raise

    return {
        **result,
        "files_restored": files_restored,
    }


def _purge_recipe_from_cart(db: Database, recipe_id: int) -> int:
    """Entfernt recipe_id aus shopping_cart.source_recipe_ids-JSON-Listen.
    Returnt Anzahl betroffener Cart-Einträge."""
    count = 0
    with db.conn() as c:
        # Alle Cart-Einträge holen die diese ID erwähnen könnten
        # (LIKE-Filter ist optimistisch — JSON-String matched)
        rows = c.execute(
            "SELECT id, source_recipe_ids FROM shopping_cart "
            "WHERE source_recipe_ids LIKE ?",
            (f"%{recipe_id}%",),
        ).fetchall()
        for row in rows:
            try:
                ids = json.loads(row["source_recipe_ids"] or "[]")
            except (ValueError, TypeError):
                ids = []
            if recipe_id in ids:
                new_ids = [i for i in ids if i != recipe_id]
                c.execute(
                    "UPDATE shopping_cart SET source_recipe_ids=? WHERE id=?",
                    (json.dumps(new_ids), row["id"]),
                )
                count += 1
    return count


# ────────────────────────────────────────────────────────────────────────
# Merge
# ────────────────────────────────────────────────────────────────────────

def safe_merge_recipes(
    db: Database,
    *,
    source_id: int,
    target_id: int,
    delete_source: bool = True,
) -> Dict[str, Any]:
    """Verschmilzt zwei Rezepte: target behält, source wird gelöscht.

    Verschoben werden:
      - Tags (Union — Duplikate werden ignoriert)
      - Shopping-Cart source_recipe_ids (source_id → target_id)

    NICHT verschoben:
      - Zutaten (target behält seine — Annahme: target ist der saubere)
      - Schritte (dito)
      - Video/Thumb-Files (target-Folder bleibt; source-Folder wird gelöscht
        wenn delete_source=True)

    delete_source=False ist ein echter Dry-Run: Es werden nur die erwarteten
    Änderungen berechnet; DB und Dateisystem bleiben unverändert.
    """
    if source_id == target_id:
        raise ValueError("source_id und target_id sind identisch")

    source = db.recipe_get(source_id)
    target = db.recipe_get(target_id)
    if not source:
        raise ValueError(f"Source #{source_id} nicht gefunden")
    if not target:
        raise ValueError(f"Target #{target_id} nicht gefunden")

    # 1. Tags vereinigen
    source_tags = [t["name"] for t in db.recipe_tags_get(source_id)]
    target_tags = [t["name"] for t in db.recipe_tags_get(target_id)]
    union_tags = sorted(set(target_tags) | set(source_tags))
    if not delete_source:
        return {
            "ok": True,
            "dry_run": True,
            "source_id": source_id,
            "target_id": target_id,
            "source_name": source.get("name"),
            "target_name": target.get("name"),
            "tags_merged": len(union_tags) - len(target_tags),
            "cart_remapped": _count_recipe_in_cart(db, source_id),
            "source_deleted": False,
        }
    if union_tags != sorted(target_tags):
        db.recipe_tags_set(target_id, union_tags)

    # 2. Cart-Refs umschreiben: source_id → target_id (mit Dedup)
    cart_remapped = _remap_recipe_in_cart(db, source_id, target_id)

    result = {
        "ok": True,
        "source_id": source_id,
        "target_id": target_id,
        "source_name": source.get("name"),
        "target_name": target.get("name"),
        "tags_merged": len(union_tags) - len(target_tags),
        "cart_remapped": cart_remapped,
        "source_deleted": False,
    }

    # 3. Source löschen (optional)
    if delete_source:
        delete_result = safe_delete_recipe(db, source_id, delete_files=True)
        result["source_deleted"] = True
        result["source_folder_deleted"] = delete_result.get("folder_deleted", False)
    logger.info(
        f"Merge #{source_id} → #{target_id}: "
        f"+{result['tags_merged']} tags, {cart_remapped} cart-refs, "
        f"source_deleted={result['source_deleted']}"
    )
    return result


def _remap_recipe_in_cart(db: Database, source_id: int, target_id: int) -> int:
    """In jeder source_recipe_ids-Liste: source_id durch target_id ersetzen
    (mit Dedup wenn target_id schon drin war). Returnt Anzahl betroffener
    Cart-Einträge."""
    count = 0
    with db.conn() as c:
        rows = c.execute(
            "SELECT id, source_recipe_ids FROM shopping_cart "
            "WHERE source_recipe_ids LIKE ?",
            (f"%{source_id}%",),
        ).fetchall()
        for row in rows:
            try:
                ids = json.loads(row["source_recipe_ids"] or "[]")
            except (ValueError, TypeError):
                ids = []
            if source_id in ids:
                new_ids = [target_id if i == source_id else i for i in ids]
                new_ids = sorted(set(new_ids))  # dedup, falls target schon drin war
                c.execute(
                    "UPDATE shopping_cart SET source_recipe_ids=? WHERE id=?",
                    (json.dumps(new_ids), row["id"]),
                )
                count += 1
    return count


def _count_recipe_in_cart(db: Database, recipe_id: int) -> int:
    """Zählt exakte JSON-Referenzen ohne einen Dry-Run zu mutieren."""
    count = 0
    with db.conn() as c:
        rows = c.execute(
            "SELECT source_recipe_ids FROM shopping_cart "
            "WHERE source_recipe_ids LIKE ?",
            (f"%{recipe_id}%",),
        ).fetchall()
    for row in rows:
        try:
            if recipe_id in json.loads(row["source_recipe_ids"] or "[]"):
                count += 1
        except (TypeError, ValueError):
            continue
    return count
