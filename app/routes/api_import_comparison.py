"""Compare incoming recipe drafts with visible recipes without external AI."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import require_auth, require_import
from ..core.safety import atomic_write_json, atomic_write_text
from ..jobs.locks import file_lock_or_none
from ..recipes import import_comparison, manage
from ..recipes.canonical import canonical_name
from ..tenant_db import HouseholdDatabase
from ..tenancy import CURRENT_HOUSEHOLD, HouseholdScope
from .api_pending import _pending_import

router = APIRouter(prefix="/api/import-comparison", tags=["import-comparison"],
                   dependencies=[Depends(require_auth), Depends(require_import)])


class Ingredient(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    amount: float | None = Field(default=None, ge=0, le=1_000_000, allow_inf_nan=False)
    unit: str | None = Field(default=None, max_length=80)
    raw: str | None = Field(default=None, max_length=1000)


class Step(BaseModel):
    instruction: str = Field(min_length=1, max_length=10_000)
    timer_seconds: int | None = Field(default=None, ge=1, le=86_400)


class Draft(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=100_000)
    servings: int | None = Field(default=None, ge=1, le=50)
    ingredients: list[Ingredient] = Field(default_factory=list, max_length=300)
    steps: list[Step] = Field(default_factory=list, max_length=200)


class Preview(BaseModel):
    url: str = Field(min_length=1, max_length=4096)
    visibility: Literal["private", "global"] = "private"
    draft: Draft


class Resolve(Preview):
    request_id: UUID
    recipe_id: int = Field(gt=0)
    snapshot: str = Field(min_length=64, max_length=64)
    action: Literal["keep", "variant", "merge"]
    name: str = Field(default="", max_length=200)
    ingredients_from: Literal["import", "existing"] = "import"
    steps_from: Literal["import", "existing"] = "existing"


def _active(body):
    db, entry, _job = _pending_import(body.url, body.visibility)
    return db, entry


@router.post("/preview")
def preview(body: Preview):
    db, entry = _active(body)
    if entry.get("status") != "pending":
        raise HTTPException(409, "Dieser Import wurde bereits abgeschlossen.")
    return import_comparison.candidates(db, entry, body.draft.model_dump())


@router.post("/resolve")
def resolve(body: Resolve):
    # Serialize with the ordinary import / discard flow, including across workers.
    with file_lock_or_none("scraper") as lock:
        if lock is None:
            raise HTTPException(409, "Ein Import läuft bereits. Bitte gleich erneut versuchen.")
        db, entry = _active(body)
        request_hash = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
        prior = (entry.get("ai_suggestion") or {}).get("comparison_result") or {}
        if prior.get("request_id") == str(body.request_id):
            if prior.get("request_hash") != request_hash:
                raise HTTPException(409, "Diese Anfrage wurde bereits mit anderen Angaben verwendet.")
            recipe = db.recipe_get(prior.get("recipe_id"))
            if recipe and recipe.get("deleted_at") is None:
                return {"ok": True, "recipe_id": recipe["id"], "replayed": True}
        if entry.get("status") != "pending":
            raise HTTPException(409, "Dieser Import wurde bereits abgeschlossen.")
        interrupted = (entry.get("ai_suggestion") or {}).get("comparison_pending") or {}
        if interrupted.get("recipe_id"):
            completed = db.recipe_get(interrupted["recipe_id"])
            if completed and completed.get("deleted_at") is None:
                folder = manage._assert_inside_root(Path(str(completed.get("folder_path") or "")))
                info = manage._variant_info(folder)
                provenance = info.get("variant_provenance") or {}
                if (completed.get("ingredients_status") != "ok" or info.get("variant_state") != "finalized"
                        or provenance.get("request_id") != interrupted.get("request_id")
                        or provenance.get("operation") != "import_comparison"
                        or not db.recipe_ingredients_get(completed["id"])
                        or not db.recipe_steps_get(completed["id"])):
                    raise HTTPException(409, "Die vorige Variante ist noch nicht vollständig. Bitte denselben Vergleich erneut übernehmen.")
                import_comparison.finish_pending(db, entry, interrupted["request_id"],
                                                 completed["id"], interrupted["request_hash"])
                if interrupted["request_id"] == str(body.request_id) and interrupted["request_hash"] == request_hash:
                    return {"ok": True, "recipe_id": completed["id"], "replayed": True}
                raise HTTPException(409, "Die vorige Entscheidung wurde bereits abgeschlossen. Bitte neu laden.")
        source = db.recipe_get(body.recipe_id)
        if not source or source.get("deleted_at") is not None:
            raise HTTPException(404, "Rezept nicht gefunden")
        try:
            with manage._recipe_mutation_lock(db, body.recipe_id):
                source = db.recipe_get(body.recipe_id)
                ingredients = db.recipe_ingredients_get(body.recipe_id)
                steps = db.recipe_steps_get(body.recipe_id)
                draft = body.draft.model_dump()
                if not source or source.get("deleted_at") is not None:
                    raise HTTPException(404, "Rezept nicht gefunden")
                if import_comparison.snapshot(draft, entry, source, ingredients, steps) != body.snapshot:
                    raise HTTPException(409, "Rezept oder Import wurden geändert. Bitte den Vergleich neu öffnen.")
                if body.action == "keep":
                    import_comparison.finish_pending(db, entry, str(body.request_id), body.recipe_id, request_hash)
                    return {"ok": True, "recipe_id": body.recipe_id}
                scope = CURRENT_HOUSEHOLD.get()
                if scope is None or scope.account_id <= 0 or scope.is_guest:
                    raise HTTPException(403, "Für eine private Variante bitte mit einem Haushaltskonto anmelden.")
                # Even administrators explicitly create a PRIVATE combined variant here.
                private_db = HouseholdDatabase(db, HouseholdScope(
                    account_id=scope.account_id, user_id=scope.user_id, is_admin=False))
                take_import_ingredients = body.action == "variant" or body.ingredients_from == "import"
                take_import_steps = body.action == "variant" or body.steps_from == "import"
                selected_ingredients = draft["ingredients"] if take_import_ingredients else ingredients
                selected_steps = draft["steps"] if take_import_steps else steps
                if not selected_ingredients or not selected_steps:
                    raise HTTPException(422, "Die gewählte Variante braucht Zutaten und Zubereitungsschritte.")
                with manage._variant_creation_lock(private_db):
                    clone = manage._safe_duplicate_recipe_serialized(
                        private_db, body.recipe_id, new_name=body.name,
                        expected_ingredients=ingredients, defer_finalization=True, final_status="ok",
                        review_notice="Importvergleich: Zutaten, Portionen und Schritte vor dem Kochen zusammen prüfen.",
                        variant_provenance={
                            "operation": "import_comparison", "request_id": str(body.request_id),
                            "existing_recipe_id": body.recipe_id, "import_url": entry.get("source_url") or entry["url"],
                            "ingredients_from": "import" if take_import_ingredients else "existing",
                            "steps_from": "import" if take_import_steps else "existing",
                            "import_draft": draft,
                        },
                    )
                    variant_id = int(clone["recipe_id"])
                    suggestion = dict(entry.get("ai_suggestion") or {})
                    suggestion["comparison_pending"] = {
                        "request_id": str(body.request_id), "recipe_id": variant_id, "request_hash": request_hash,
                    }
                    db.pending_update_suggestion(body.url, suggestion)
                    entry["ai_suggestion"] = suggestion
                    from .api_recipes import _replace_ingredients_and_reset_verification
                    prepared = [{**item, "canonical_name": canonical_name(item["name"])} for item in selected_ingredients]
                    _replace_ingredients_and_reset_verification(private_db, variant_id, prepared,
                                                               ingredients_status="variant_pending")
                    private_db.recipe_steps_set(variant_id, selected_steps)
                    servings = draft["servings"] if take_import_ingredients else source.get("servings")
                    private_db.recipe_set_servings(variant_id, servings)
                    # A different ingredient list cannot inherit dietary claims or auto tags.
                    private_db.recipe_tags_set(variant_id, [])
                    private_db.recipe_auto_tags_set(variant_id, [])
                    folder = Path(clone["folder_path"])
                    description = draft["description"] if take_import_steps else (source.get("description") or "")
                    with private_db.conn() as connection:
                        connection.execute("UPDATE recipes SET description=? WHERE id=? AND owner_account_id=?",
                                           (description, variant_id, scope.account_id))
                    atomic_write_text(folder / "description.txt", description)
                    info_path = folder / "info.json"
                    info = json.loads(info_path.read_text(encoding="utf-8"))
                    info.update({"ingredients": prepared, "steps": selected_steps,
                                 "servings": servings, "description": description})
                    atomic_write_json(info_path, info)
                    manage.finalize_recipe_variant(private_db, variant_id, final_status="ok")
                    import_comparison.finish_pending(db, entry, str(body.request_id), variant_id, request_hash)
                    return {"ok": True, "recipe_id": variant_id}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except (RuntimeError, OSError) as exc:
            raise HTTPException(409, "Die Variante konnte nicht abgeschlossen werden. Bitte neu laden und erneut versuchen.") from exc
