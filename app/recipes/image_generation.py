"""Reversible Rezeptbild-Generierung mit vorgeschalteter Originalsicherung."""
from __future__ import annotations

import os
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


_BATCH_RE = re.compile(r"^[A-Za-z0-9_-]{8,80}$")


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
    filename = Path(str(recipe.get("thumb_filename") or "")).name
    if not filename:
        candidates = sorted(
            path for path in folder.iterdir()
            if path.is_file()
            and not path.is_symlink()
            and path.suffix.casefold() in {".jpg", ".jpeg", ".png", ".webp"}
            and not path.name.startswith("thumb-w")
        )
        filename = candidates[0].name if candidates else ""
    if not filename:
        return None
    source = resolve_regular_file_under(folder / filename, folder, _recipe_root())
    checksum = sha256_file(source)
    root = image_backup_root()
    suffix = source.suffix.lower() if source.suffix else ".img"
    relative = Path(batch_id) / str(int(recipe["id"])) / f"original{suffix}"
    destination = root / relative
    existing = next(
        (
            item for item in get_db().recipe_image_backup_list(int(recipe["id"]), limit=1000)
            if item.get("batch_id") == batch_id
        ),
        None,
    )
    if existing:
        stored = resolve_regular_file_under(root / str(existing["backup_path"]), root)
        if sha256_file(stored) != str(existing["original_sha256"]):
            raise RuntimeError(f"Bildsicherung #{existing['id']} hat eine abweichende Prüfsumme")
        return int(existing["id"])
    atomic_write_bytes(destination, source.read_bytes())
    if sha256_file(destination) != checksum:
        destination.unlink(missing_ok=True)
        raise RuntimeError("Prüfsumme der Bildsicherung stimmt nicht überein")
    backup_id = get_db().recipe_image_backup_create(
        batch_id=batch_id,
        recipe_id=int(recipe["id"]),
        original_filename=filename,
        backup_path=relative.as_posix(),
        original_sha256=checksum,
    )
    get_db().recipe_image_generation_status(
        int(recipe["id"]), status="backed_up", batch_id=batch_id
    )
    return backup_id


def generate_recipe_image(recipe_id: int, *, batch_id: Optional[str] = None) -> Dict[str, Any]:
    db = get_db()
    recipe = db.recipe_get(int(recipe_id))
    if not recipe or recipe.get("deleted_at") is not None:
        raise LookupError("Rezept nicht gefunden")
    batch_id = _batch_id(batch_id)
    settings = ensure_image_generation_configured()
    # Auch die Einzelgenerierung darf ein vorhandenes Bild nie ohne Sicherung ersetzen.
    backup_id = backup_recipe_image(recipe, batch_id)
    prompt = build_recipe_image_prompt(recipe, db.recipe_ingredients_get(int(recipe_id)))
    db.recipe_image_generation_status(
        int(recipe_id), status="running", model=settings["model"],
        prompt=prompt, batch_id=batch_id,
    )
    folder = _recipe_folder(recipe)
    raw = folder / f".generated-{uuid.uuid4().hex}.img"
    staged = folder / f".generated-{uuid.uuid4().hex}.jpg"
    target = folder / "thumb-generated.jpg"
    rollback = folder / f".generated-rollback-{uuid.uuid4().hex}.jpg"
    had_target = target.is_file()
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
        if had_target:
            os.replace(target, rollback)
        os.replace(staged, target)
        try:
            db.recipe_image_generation_status(
                int(recipe_id), status="ok", model=settings["model"], prompt=prompt,
                batch_id=batch_id, generated_at=time.time(), thumb_filename=target.name,
            )
            if backup_id is not None:
                db.recipe_image_backup_mark_generated(
                    backup_id, generated_sha256=generated_sha256,
                    model=settings["model"], prompt=prompt,
                )
        except Exception:
            target.unlink(missing_ok=True)
            if had_target and rollback.exists():
                os.replace(rollback, target)
            raise
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
    except Exception:
        db.recipe_image_generation_status(
            int(recipe_id), status="error", model=settings["model"],
            prompt=prompt, batch_id=batch_id,
        )
        raise
    finally:
        raw.unlink(missing_ok=True)
        staged.unlink(missing_ok=True)
        rollback.unlink(missing_ok=True)


def run_image_backfill(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Sichert erst den vollständigen Altbestand, generiert danach Bilder."""
    db = get_db()
    run_id = int(payload["run_id"])
    batch_id = _batch_id(str(payload["batch_id"]))
    recipes = db.recipes_for_image_backfill()
    total = len(recipes)
    backed_up = 0
    try:
        ensure_image_generation_configured()
        db.maintenance_progress(
            run_id,
            {"phase": "backup", "batch_id": batch_id, "total": total, "backed_up": 0},
        )
        # Sicherheitsbarriere: Bei genau einem Sicherungsfehler startet keine
        # Bildgenerierung. So bleibt der Altbestand als geschlossene Serie erhalten.
        for index, recipe in enumerate(recipes, start=1):
            if backup_recipe_image(recipe, batch_id) is not None:
                backed_up += 1
            db.maintenance_progress(
                run_id,
                {
                    "phase": "backup", "batch_id": batch_id, "total": total,
                    "processed": index, "backed_up": backed_up,
                },
            )

        generated = 0
        errors: list[dict] = []
        for index, recipe in enumerate(recipes, start=1):
            try:
                generate_recipe_image(int(recipe["id"]), batch_id=batch_id)
                generated += 1
            except Exception as exc:
                errors.append({
                    "recipe_id": int(recipe["id"]),
                    "name": recipe.get("name"),
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                })
            db.maintenance_progress(
                run_id,
                {
                    "phase": "generate", "batch_id": batch_id, "total": total,
                    "processed": index, "backed_up": backed_up,
                    "generated": generated, "errors": errors[-20:],
                },
            )
        result = {
            "ok": not errors,
            "phase": "done",
            "batch_id": batch_id,
            "total": total,
            "backed_up": backed_up,
            "generated": generated,
            "error_count": len(errors),
            "errors": errors,
        }
        db.maintenance_finish(run_id, ok=not errors, result=result)
        return result
    except Exception as exc:
        result = {
            "ok": False,
            "phase": "backup_failed",
            "batch_id": batch_id,
            "total": total,
            "backed_up": backed_up,
            "generated": 0,
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
    atomic_write_bytes(target, source.read_bytes())
    if sha256_file(target) != str(backup["original_sha256"]):
        raise RuntimeError("Wiederhergestelltes Bild hat eine abweichende Prüfsumme")
    db.recipe_image_generation_status(
        int(recipe["id"]), status="restored", batch_id=str(backup["batch_id"]),
        thumb_filename=filename,
    )
    db.recipe_image_backup_mark_restored(int(backup_id))
    invalidate_thumbnail_cache(folder)
    return {
        "ok": True,
        "recipe_id": int(recipe["id"]),
        "backup_id": int(backup_id),
        "thumbnail": filename,
        "sha256": backup["original_sha256"],
    }
