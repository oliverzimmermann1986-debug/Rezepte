"""Reversible Rezeptbild-Generierung mit vorgeschalteter Originalsicherung."""
from __future__ import annotations

import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from ..config_store import get_config
from ..core.analyzer import build_analyzer
from ..core.safety import (
    atomic_write_bytes,
    resolve_directory_under,
    resolve_regular_file_under,
    sha256_file,
)
from ..db import get_db
from .image_cache import invalidate_thumbnail_cache, normalize_image
from .image_publish import image_publication_lock, publish_image


_BATCH_RE = re.compile(r"^[A-Za-z0-9_-]{8,80}$")


class _ImageSuperseded(Exception):
    """Der Auftrag darf einen inzwischen geänderten Bildstand nicht ersetzen."""


def _skipped_generation(recipe_id: int, batch_id: str) -> Dict[str, Any]:
    return {
        "ok": True, "skipped": True, "recipe_id": recipe_id, "batch_id": batch_id,
        "reason": "Rezeptbild oder Bildauftrag wurde inzwischen geändert",
    }


def image_backup_root() -> Path:
    cfg = get_config()
    configured = cfg.get("paths", "data_dir", default=str(get_db().path.parent))
    root = Path(configured or get_db().path.parent) / "recipe-image-originals"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve(strict=True)


def _recipe_root() -> Path:
    return Path(get_config().get("paths", "recipe_dir", default="/mnt/rezepte"))


def _recipe_folder(recipe: Dict[str, Any]) -> Path:
    return resolve_directory_under(Path(recipe["folder_path"]), _recipe_root())


def _batch_id(value: Optional[str] = None) -> str:
    batch_id = value or uuid.uuid4().hex
    if not _BATCH_RE.fullmatch(batch_id):
        raise ValueError("Ungültige Bild-Batch-ID")
    return batch_id


def image_generation_settings() -> Dict[str, Any]:
    cfg = get_config()
    settings = cfg.get("ai", "image_generation", default={}) or {}
    return {
        "enabled": bool(settings.get("enabled", True)),
        "model": str(settings.get("model") or "gpt-image-2").strip(),
        "size": str(settings.get("size") or "1536x1024").strip(),
        "quality": str(settings.get("quality") or "medium").strip(),
        "output_format": str(settings.get("output_format") or "jpeg").strip(),
    }


def ensure_image_generation_configured() -> Dict[str, Any]:
    settings = image_generation_settings()
    if not settings["enabled"]:
        raise ValueError("Rezeptbild-Generierung ist in den Einstellungen deaktiviert")
    build_analyzer(get_config().get("ai", default={}) or {})
    return settings


def build_recipe_image_prompt(recipe: Dict[str, Any], ingredients: list[dict]) -> str:
    ingredient_names = [
        str(item.get("name") or "").strip()
        for item in ingredients
        if str(item.get("name") or "").strip()
    ][:14]
    context = ", ".join(ingredient_names)
    description = " ".join(str(recipe.get("description") or "").split())[:500]
    return (
        "Create a realistic premium food photograph for the German recipe "
        f"'{recipe.get('name') or 'Rezept'}'. "
        f"Dish type: {recipe.get('type') or 'unknown'}; category: "
        f"{recipe.get('category') or 'unknown'}. "
        + (f"Visible key ingredients: {context}. " if context else "")
        + (f"Recipe context: {description}. " if description else "")
        + "Natural appetizing plating, soft daylight, authentic edible textures, "
        "slightly elevated three-quarter camera angle, horizontal composition with "
        "the complete dish centered. No people, no hands, no packaging, no logos, "
        "no text, no watermark, no collage."
    )


def backup_recipe_image(recipe: Dict[str, Any], batch_id: str) -> Optional[int]:
    """Sichert das aktuell aktive Bild idempotent und checksummiert."""
    batch_id = _batch_id(batch_id)
    folder = _recipe_folder(recipe)
    with image_publication_lock(folder):
        current = get_db().recipe_get(int(recipe["id"]))
        if not current or current.get("deleted_at") is not None:
            raise LookupError("Rezept nicht gefunden")
        return _backup_recipe_image(current, batch_id, folder)


def _backup_recipe_image(
    recipe: Dict[str, Any], batch_id: str, folder: Path, *, mark_prepared: bool = True,
) -> Optional[int]:
    """Aufrufer hält den Ordner-Lock; bei Generierung bleibt der Claim unverändert."""
    db = get_db()
    existing = db.recipe_image_backup_for_batch(int(recipe["id"]), batch_id)
    if existing:
        root = image_backup_root()
        stored = resolve_regular_file_under(root / str(existing["backup_path"]), root)
        if sha256_file(stored) != str(existing["original_sha256"]):
            raise RuntimeError(f"Bildsicherung #{existing['id']} hat eine abweichende Prüfsumme")
        return int(existing["id"])
    filename = Path(str(recipe.get("thumb_filename") or "")).name
    if not filename:
        candidates = sorted(
            path for path in folder.iterdir()
            if path.is_file()
            and not path.is_symlink()
            and not path.name.startswith(".")
            and path.suffix.casefold() in {".jpg", ".jpeg", ".png", ".webp"}
            and not path.name.startswith("thumb-w")
        )
        filename = candidates[0].name if candidates else ""
    backup_id = None
    if filename:
        source = resolve_regular_file_under(folder / filename, folder, _recipe_root())
        checksum = sha256_file(source)
        root = image_backup_root()
        suffix = source.suffix.lower() if source.suffix else ".img"
        relative = Path(batch_id) / str(int(recipe["id"])) / f"original{suffix}"
        destination = root / relative
        atomic_write_bytes(destination, source.read_bytes())
        if sha256_file(destination) != checksum:
            destination.unlink(missing_ok=True)
            raise RuntimeError("Prüfsumme der Bildsicherung stimmt nicht überein")
        backup_id = db.recipe_image_backup_create(
            batch_id=batch_id,
            recipe_id=int(recipe["id"]),
            original_filename=filename,
            backup_path=relative.as_posix(),
            original_sha256=checksum,
        )
    if mark_prepared:
        db.recipe_image_generation_status(
            int(recipe["id"]), status="backed_up", batch_id=batch_id,
            expected_batch_id=recipe.get("image_generation_batch_id"),
            expected_status=recipe["image_generation_status"],
        )
    return backup_id


def generate_recipe_image(
    recipe_id: int, *, batch_id: Optional[str] = None, queued: bool = False,
    replace_existing: bool = False,
) -> Dict[str, Any]:
    db = get_db()
    recipe_id = int(recipe_id)
    recipe = db.recipe_get(recipe_id)
    if not recipe or recipe.get("deleted_at") is not None:
        raise LookupError("Rezept nicht gefunden")
    batch_id = _batch_id(batch_id)
    folder = _recipe_folder(recipe)
    with image_publication_lock(folder):
        recipe = db.recipe_get(recipe_id)
        if not recipe or recipe.get("deleted_at") is not None:
            raise LookupError("Rezept nicht gefunden")
        # Ältere, noch nicht markierte pending-Aufträge bleiben nach einem Update
        # ausführbar. Ein manueller Wechsel/restored-Status entzieht den Claim.
        if queued and (
            recipe.get("image_generation_status") not in {"pending", "backed_up", "running", "error"}
            or not (
                recipe.get("image_generation_batch_id") == batch_id
                or (recipe.get("image_generation_batch_id") is None
                    and recipe.get("image_generation_status") == "pending")
            )
        ):
            return _skipped_generation(recipe_id, batch_id)
        if queued and not replace_existing and recipe.get("thumb_filename"):
            try:
                resolve_regular_file_under(folder / str(recipe["thumb_filename"]), folder)
            except (ValueError, OSError):
                pass
            else:
                db.recipe_image_generation_status(
                    recipe_id, status="skipped", expected_batch_id=recipe.get("image_generation_batch_id"),
                    expected_status=recipe["image_generation_status"],
                )
                return {**_skipped_generation(recipe_id, batch_id), "reason": "Vorhandenes Quellbild bleibt erhalten"}
        settings = ensure_image_generation_configured()
        # Sicherung und Claim müssen denselben Bildstand sehen. Netzwerk-/KI-Arbeit
        # erfolgt danach ohne Ordner-Lock, damit manuelle Uploads möglich bleiben.
        backup_id = _backup_recipe_image(recipe, batch_id, folder, mark_prepared=False)
        prompt = build_recipe_image_prompt(recipe, db.recipe_ingredients_get(recipe_id))
        if not db.recipe_image_generation_status(
            recipe_id, status="running", model=settings["model"], prompt=prompt, batch_id=batch_id,
            expected_batch_id=recipe.get("image_generation_batch_id"),
            expected_status=recipe["image_generation_status"],
        ):
            return _skipped_generation(recipe_id, batch_id)
    raw = folder / f".generated-{uuid.uuid4().hex}.img"
    staged = folder / f".generated-{uuid.uuid4().hex}.jpg"
    target = folder / "thumb-generated.jpg"

    def validate_publication() -> None:
        current = db.recipe_get(recipe_id)
        if not current or current.get("deleted_at") is not None or (
            current.get("image_generation_batch_id") != batch_id
            or current.get("image_generation_status") != "running"
        ):
            raise _ImageSuperseded()

    try:
        analyzer = build_analyzer(get_config().get("ai", default={}) or {})
        generated = analyzer.generate_recipe_image(
            prompt,
            model=settings["model"],
            size=settings["size"],
            quality=settings["quality"],
            output_format=settings["output_format"],
        )
        atomic_write_bytes(raw, generated)
        normalize_image(raw, staged, max_width=2400, quality=90)
        generated_sha256 = sha256_file(staged)
        with publish_image(staged, target, validate=validate_publication):
            if not db.recipe_image_generation_status(
                recipe_id, status="ok", model=settings["model"], prompt=prompt,
                batch_id=batch_id, generated_at=time.time(), thumb_filename=target.name,
                backup_id=backup_id, generated_sha256=generated_sha256,
                expected_batch_id=batch_id, expected_status="running",
            ):
                raise _ImageSuperseded()
        invalidate_thumbnail_cache(folder)
        return {
            "ok": True,
            "recipe_id": int(recipe_id),
            "batch_id": batch_id,
            "backup_id": backup_id,
            "thumbnail": target.name,
            "model": settings["model"],
            "sha256": generated_sha256,
        }
    except _ImageSuperseded:
        return _skipped_generation(recipe_id, batch_id)
    except Exception:
        if not db.recipe_image_generation_status(
            recipe_id, status="error", model=settings["model"],
            prompt=prompt, batch_id=batch_id,
            expected_batch_id=batch_id, expected_status="running",
        ):
            return _skipped_generation(recipe_id, batch_id)
        raise
    finally:
        raw.unlink(missing_ok=True)
        staged.unlink(missing_ok=True)


def run_image_backfill(
    payload: Dict[str, Any], *, chunk_size: Optional[int] = None,
) -> Dict[str, Any]:
    """Sichert den Altbestand vorab und setzt gespeicherte Teilschritte fort.

    Der Worker verarbeitet pro Claim höchstens ein Rezept. Die direkte Ausführung
    bleibt für Wartungswerkzeuge möglich. Neue Rezepte werden nicht nachträglich
    in eine bereits gesicherte Serie aufgenommen.
    """
    db = get_db()
    run_id = int(payload["run_id"])
    batch_id = _batch_id(str(payload["batch_id"]))
    run = db.maintenance_get(run_id)
    if not run:
        raise LookupError("Bild-Wartungslauf nicht gefunden")
    saved = run.get("result") or {}
    if saved.get("batch_id") == batch_id and saved.get("phase") == "done":
        return saved
    if run["status"] != "running":
        raise RuntimeError("Bild-Wartungslauf ist nicht mehr aktiv")
    if saved.get("batch_id") == batch_id and "recipe_ids" in saved:
        state = saved
    else:
        state = {
            "phase": "backup", "batch_id": batch_id,
            "recipe_ids": (list(payload["recipe_ids"]) if "recipe_ids" in payload else
                           [int(recipe["id"]) for recipe in db.recipes_for_image_backfill(ids_only=True)]),
            "backup_processed": 0, "backed_up": 0, "generated": 0,
            "completed_ids": [], "errors": [],
        }
    state.setdefault("skipped_ids", [])
    recipe_ids = state["recipe_ids"]
    state["total"] = total = len(recipe_ids)
    budget = max(1, int(chunk_size)) if chunk_size is not None else total * 2 + 1
    try:
        ensure_image_generation_configured()
        db.maintenance_progress(run_id, state)
        # Sicherheitsbarriere: Bei genau einem Sicherungsfehler startet keine
        # Bildgenerierung. So bleibt der Altbestand als geschlossene Serie erhalten.
        while state["backup_processed"] < total and budget:
            recipe_id = recipe_ids[state["backup_processed"]]
            recipe = db.recipe_get(recipe_id)
            if not recipe or recipe.get("deleted_at") is not None:
                raise LookupError(f"Rezept #{recipe_id} fehlt vor der Originalsicherung")
            if backup_recipe_image(recipe, batch_id) is not None:
                state["backed_up"] += 1
            state["backup_processed"] += 1
            state["processed"] = state["backup_processed"]
            budget -= 1
            db.maintenance_progress(run_id, state)
        if state["backup_processed"] < total:
            return {"continue": True, "phase": "backup"}

        state["phase"] = "generate"
        completed = set(state["completed_ids"])
        for recipe_id in recipe_ids:
            if recipe_id in completed:
                continue
            if not budget:
                db.maintenance_progress(run_id, state)
                return {"continue": True, "phase": "generate"}
            recipe = db.recipe_get(recipe_id) or {"id": recipe_id}
            try:
                # Ein Absturz zwischen Bild-Commit und Checkpoint darf kein
                # bereits erfolgreich erzeugtes Bild erneut kostenpflichtig erzeugen.
                already_generated = (
                    recipe.get("image_generation_batch_id") == batch_id
                    and recipe.get("image_generation_status") == "ok"
                )
                generated = None if already_generated else generate_recipe_image(
                    recipe_id, batch_id=batch_id, queued=True, replace_existing=True,
                )
                if generated and generated.get("skipped"):
                    state["skipped_ids"].append(recipe_id)
                else:
                    state["generated"] += 1
            except Exception as exc:
                state["errors"].append({
                    "recipe_id": recipe_id,
                    "name": recipe.get("name"),
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                })
            state["completed_ids"].append(recipe_id)
            state["processed"] = len(state["completed_ids"])
            budget -= 1
            db.maintenance_progress(run_id, state)
        result = {
            **state, "ok": not state["errors"], "phase": "done",
            "error_count": len(state["errors"]),
        }
        db.maintenance_finish(run_id, ok=result["ok"], result=result)
        return result
    except Exception as exc:
        result = {
            **state, "ok": False,
            "phase": "backup_failed" if state["phase"] == "backup" else "generate_failed",
            "error": f"{type(exc).__name__}: {exc}"[:500],
        }
        db.maintenance_finish(run_id, ok=False, result=result)
        return result


def restore_recipe_image_backup(backup_id: int) -> Dict[str, Any]:
    db = get_db()
    backup = db.recipe_image_backup_get(int(backup_id))
    if not backup or not backup.get("folder_path"):
        raise LookupError("Bildsicherung oder Rezept nicht gefunden")
    recipe = db.recipe_get(int(backup["recipe_id"]))
    if not recipe:
        raise LookupError("Rezept nicht gefunden")
    root = image_backup_root()
    source = resolve_regular_file_under(root / str(backup["backup_path"]), root)
    if sha256_file(source) != str(backup["original_sha256"]):
        raise RuntimeError("Bildsicherung ist beschädigt (Prüfsumme stimmt nicht)")
    folder = _recipe_folder(recipe)
    filename = Path(str(backup["original_filename"])).name
    target = folder / filename
    staged = folder / f".thumb-restore-{uuid.uuid4().hex}.jpg"
    try:
        atomic_write_bytes(staged, source.read_bytes())
        if sha256_file(staged) != str(backup["original_sha256"]):
            raise RuntimeError("Wiederhergestelltes Bild hat eine abweichende Prüfsumme")
        with publish_image(staged, target):
            db.recipe_image_generation_status(
                int(recipe["id"]), status="restored", batch_id=str(backup["batch_id"]),
                thumb_filename=filename, backup_id=int(backup_id),
            )
    finally:
        staged.unlink(missing_ok=True)
    invalidate_thumbnail_cache(folder)
    return {
        "ok": True,
        "recipe_id": int(recipe["id"]),
        "backup_id": int(backup_id),
        "thumbnail": filename,
        "sha256": backup["original_sha256"],
    }
