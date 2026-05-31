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
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config_store import get_config
from ..db import Database

logger = logging.getLogger(__name__)


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
    """Benennt ein Rezept um. Atomisch über DB + FS.

    rename_folder=True (Default):
      - Folder wird umbenannt (auf _sanitize(new_name))
      - Dateien innerhalb (name.mp4, name.jpg) bekommen den neuen Namen
      - info.json.name + DB-thumb_filename + DB-video_filename werden mit angepasst
      - Bei Konflikt (Ziel-Folder existiert): Abbruch mit RuntimeError

    rename_folder=False:
      - Nur das DB-Feld `name` ändert sich (Display-Name)
      - Folder + Files bleiben unverändert

    Returns: {ok, recipe_id, old_name, new_name, old_folder?, new_folder?}
    """
    new_name = (new_name or "").strip()
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

    result: Dict[str, Any] = {
        "ok": True,
        "recipe_id": recipe_id,
        "old_name": old_name,
        "new_name": new_name,
    }

    if not rename_folder:
        # Nur Display-Name. info.json bleibt unangetastet — dort steht
        # der Original-Name beim Scrape. Das ist OK; der User wollte
        # eben NICHT den Folder umbenennen.
        with db.conn() as c:
            c.execute("UPDATE recipes SET name=? WHERE id=?", (new_name, recipe_id))
        logger.info(f"Recipe #{recipe_id}: name '{old_name}' → '{new_name}' (FS unverändert)")
        return result

    # rename_folder=True
    if not old_folder:
        raise ValueError(f"Recipe #{recipe_id} hat keinen folder_path")
    old_dir = Path(old_folder)
    _assert_inside_root(old_dir)
    if not old_dir.exists():
        # FS-Folder fehlt — wahrscheinlich manuell gelöscht. DB-only-Rename
        # ist trotzdem sicher (kein FS-Touch, nichts zu konflikten).
        logger.warning(
            f"Recipe #{recipe_id}: folder {old_dir} existiert nicht, "
            f"nur DB-Rename"
        )
        with db.conn() as c:
            c.execute("UPDATE recipes SET name=? WHERE id=?", (new_name, recipe_id))
        result["warning"] = "Folder existierte nicht — nur DB aktualisiert"
        return result

    new_dir_name = sanitize_filename(new_name)
    new_dir = old_dir.parent / new_dir_name
    _assert_inside_root(new_dir)

    if new_dir.exists() and new_dir != old_dir:
        raise RuntimeError(
            f"Ziel-Folder existiert bereits: {new_dir}. "
            f"Anderen Namen wählen oder den existierenden zuerst aufräumen."
        )

    # 1. Folder verschieben
    if new_dir != old_dir:
        old_dir.rename(new_dir)
        logger.info(f"Recipe #{recipe_id}: folder {old_dir} → {new_dir}")

    # 2. Files innerhalb umbenennen — alle die mit dem alten Folder-Namen
    #    anfangen (z.B. "Lasagne.mp4", "Lasagne.jpg"). description.txt +
    #    info.json bleiben generisch und müssen nicht umbenannt werden.
    old_file_base = old_dir.name
    new_video_filename = recipe.get("video_filename")
    new_thumb_filename = recipe.get("thumb_filename")
    for f in new_dir.iterdir():
        if not f.is_file():
            continue
        # Nur Files umbenennen die exakt mit dem alten Folder-Namen + Suffix anfangen
        if f.stem == old_file_base:
            new_path = new_dir / f"{new_dir_name}{f.suffix}"
            f.rename(new_path)
            # DB-Filename-Felder mitziehen
            if recipe.get("video_filename") == f.name:
                new_video_filename = new_path.name
            if recipe.get("thumb_filename") == f.name:
                new_thumb_filename = new_path.name

    # 3. info.json updaten (falls vorhanden)
    info_file = new_dir / "info.json"
    if info_file.exists():
        try:
            info = json.loads(info_file.read_text(encoding="utf-8"))
            info["name"] = new_name
            info_file.write_text(
                json.dumps(info, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"Recipe #{recipe_id}: info.json update failed: {e}")

    # 4. DB updaten
    with db.conn() as c:
        c.execute(
            "UPDATE recipes SET name=?, folder_path=?, video_filename=?, thumb_filename=? "
            "WHERE id=?",
            (new_name, str(new_dir), new_video_filename, new_thumb_filename, recipe_id),
        )

    result["old_folder"] = str(old_dir)
    result["new_folder"] = str(new_dir)
    return result


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
    """Löscht ein Rezept. Standardmäßig SOFT-DELETE (→ Papierkorb).

    - hard=False (Default): Soft-Delete — deleted_at=now, Rezept verschwindet
      aus Listings, taucht im Papierkorb auf. Wird nach 30 Tagen via
      Cleanup-Job endgültig entfernt. Restore möglich via recipe_restore.
    - hard=True: Endgültiges Löschen (HARD-DELETE) — DB-Zeile weg, cascade
      auf ingredients/steps/tags, optional auch FS-Folder. Verwendet vom
      Papierkorb-Endpunkt 'endgültig löschen' und vom Cleanup-Job.

    - delete_files=True: bei soft-delete wird der Folder zusätzlich entfernt
      (files_deleted=1 gesetzt — Restore kann Files dann nicht wiederherstellen).
      Bei hard-delete wird der Folder gelöscht falls er noch existiert.

    Cart wird bei BEIDEN Modi aufgeräumt (Cart-Einträge die auf das Rezept
    referenzieren werden upgedatet), damit Cart nicht auf 'unsichtbare'
    Rezepte zeigt.

    Returns: {ok, deleted_id, name, folder_deleted, cart_entries_updated, soft}
    """
    recipe = db.recipe_get(recipe_id)
    if not recipe:
        raise ValueError(f"Recipe #{recipe_id} nicht gefunden")
    folder = recipe.get("folder_path")
    name = recipe.get("name")

    # Cart aufräumen
    cart_updates = _purge_recipe_from_cart(db, recipe_id)

    # FS löschen (bei delete_files=True, beide Modi)
    folder_deleted = False
    if delete_files and folder:
        folder_path = Path(folder)
        _assert_inside_root(folder_path)
        if folder_path.exists():
            shutil.rmtree(folder_path)
            folder_deleted = True
            logger.info(f"Recipe #{recipe_id}: folder {folder_path} gelöscht")
        else:
            logger.info(f"Recipe #{recipe_id}: folder {folder_path} existierte nicht mehr")

    if hard:
        # Endgültig: cascade weg
        db.recipe_delete(recipe_id)
        logger.info(f"Recipe #{recipe_id} '{name}' HARD-DELETE")
    else:
        # Soft: deleted_at setzen, files_deleted-Flag wenn Folder mit weg
        db.recipe_soft_delete(recipe_id, files_deleted=folder_deleted)
        logger.info(f"Recipe #{recipe_id} '{name}' → Papierkorb (files_deleted={folder_deleted})")

    return {
        "ok": True,
        "deleted_id": recipe_id,
        "name": name,
        "folder_deleted": folder_deleted,
        "cart_entries_updated": cart_updates,
        "soft": not hard,
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

    delete_source=False ist ein „dry-run"-Modus: target kriegt die Tags +
    Cart-Refs, source bleibt vollständig erhalten. Nützlich bei Unsicherheit.
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
