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
from ..core.safety import atomic_write_bytes, atomic_write_json, atomic_write_text
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
        # Der Ordnername bleibt unverändert, der Display-Name muss aber auch
        # im Sidecar stehen. Sonst setzt der nächste FS-Sync den User-Edit
        # wieder auf den alten importierten Namen zurück.
        if old_folder:
            info_file = Path(old_folder) / "info.json"
            if info_file.parent.exists():
                try:
                    info = (
                        json.loads(info_file.read_text(encoding="utf-8"))
                        if info_file.exists() else {}
                    )
                    info["name"] = new_name
                    atomic_write_json(info_file, info)
                except Exception as e:
                    raise RuntimeError(f"info.json konnte nicht aktualisiert werden: {e}") from e
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
            atomic_write_json(info_file, info)
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


def safe_update_recipe_metadata(
    db: Database,
    recipe_id: int,
    *,
    name: str,
    recipe_type: str,
    category: str,
    description: str,
    servings: Optional[int],
    url: Optional[str],
) -> Dict[str, Any]:
    """Aktualisiert sichtbare Metadaten konsistent in DB, Sidecars und Pfad.

    Typ/Kategorie/Name bilden die Ordnerstruktur. Deshalb wird der Rezeptordner
    bei Bedarf mit verschoben. Scheitert danach ein Sidecar- oder DB-Schritt,
    werden Dateinamen, Sidecars und Ordner bestmöglich auf den Ausgangsstand
    zurückgerollt.
    """
    values = {
        "name": (name or "").strip(),
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
        target_folder = root / sanitize_filename(values["type"]) / sanitize_filename(values["category"]) / sanitize_filename(values["name"])
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

    if hard and not delete_files and folder:
        candidate = Path(folder)
        if candidate.exists():
            folder_path = _assert_inside_root(candidate)
            raise RuntimeError(
                "Hard-Delete ohne Dateilöschung abgelehnt: "
                "der verbleibende Ordner würde beim nächsten Sync erneut indiziert"
            )

    # Cart aufräumen
    cart_updates = _purge_recipe_from_cart(db, recipe_id)

    # FS: nicht mehr hart löschen — in Quarantäne verschieben (Härtung gegen
    # Datenverlust). deleted_history bewahrt Herkunft für spätere Suche/Restore.
    folder_deleted = False
    if delete_files and folder:
        folder_path = Path(folder)
        _assert_inside_root(folder_path)
        if folder_path.exists():
            from ..core.safety import quarantine_move
            trash_root = Path(get_config().get("safety", "trash_dir",
                              default="/opt/scrapper/data/trash"))
            qpath = quarantine_move(folder_path, trash_root,
                                    reason="hard_delete" if hard else "soft_delete",
                                    source={"recipe_id": recipe_id, "name": name})
            folder_deleted = True
            try:
                db.deleted_history_add(
                    {"url": recipe.get("url"), "content_type": recipe.get("type"),
                     "name": name, "target_dir": folder},
                    quarantine_path=str(qpath or ""),
                    reason="hard_delete" if hard else "soft_delete")
            except Exception as e:
                logger.warning(f"deleted_history_add fehlgeschlagen (non-fatal): {e}")
            logger.info(f"Recipe #{recipe_id}: folder → Quarantäne {qpath}")
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


def safe_restore_recipe(db: Database, recipe_id: int) -> Dict[str, Any]:
    """Stellt DB-Eintrag und gegebenenfalls Quarantäneordner gemeinsam her."""
    recipe = db.recipe_get(recipe_id)
    if not recipe:
        raise ValueError(f"Recipe #{recipe_id} nicht gefunden")
    if recipe.get("deleted_at") is None:
        return db.recipe_restore(recipe_id)

    folder = _assert_inside_root(Path(recipe["folder_path"]))
    files_deleted = bool(recipe.get("files_deleted"))
    moved_from: Optional[Path] = None

    if files_deleted:
        if folder.is_dir():
            # Ein manueller Repair kann den Ordner bereits zurückgebracht haben.
            files_restored = True
        else:
            history = db.deleted_history_latest(
                str(recipe["folder_path"]),
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
