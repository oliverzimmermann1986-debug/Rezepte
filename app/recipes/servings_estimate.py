"""Reviewable AI portion estimates and atomic, versioned application."""
from __future__ import annotations

import hashlib
import json
import math
import time
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ..db import RECIPE_VARIANT_PENDING_STATUS
from ..tenancy import CURRENT_HOUSEHOLD
from .canonical import canonical_name

Portions = Annotated[int, Field(ge=1, le=50, strict=True)]
WarningText = Annotated[str, Field(min_length=1, max_length=300, strict=True)]


class EstimateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    suggested_servings: Portions | None
    min_servings: Portions | None
    max_servings: Portions | None
    confidence: Literal["low", "medium", "high"]
    rationale: str = Field(min_length=1, max_length=1200, strict=True)
    warnings: list[WarningText] = Field(max_length=8)

    @model_validator(mode="after")
    def consistent_range(self):
        if not self.rationale.strip():
            raise ValueError("Leere Begründung")
        if self.suggested_servings is None:
            if self.min_servings is not None or self.max_servings is not None or self.confidence != "low":
                raise ValueError("Ohne Schätzung darf keine Bandbreite oder Sicherheit behauptet werden")
        elif (self.min_servings is None or self.max_servings is None
              or not self.min_servings <= self.suggested_servings <= self.max_servings):
            raise ValueError("Die Bandbreite muss die geschätzte Portionszahl enthalten")
        return self


def _snapshot(connection, db, recipe_id):
    scope = getattr(db, "scope", None) or CURRENT_HOUSEHOLD.get()
    account_id = int(getattr(db, "account_id", 0))
    row = connection.execute(
        "SELECT id,name,type,category,description,servings,owner_account_id,ingredients_status FROM recipes "
        "WHERE id=? AND deleted_at IS NULL AND COALESCE(ingredients_status,'')<>? "
        "AND (owner_account_id IS NULL OR owner_account_id=?)",
        (recipe_id, RECIPE_VARIANT_PENDING_STATUS, account_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Rezept nicht gefunden")
    recipe = dict(row)
    if scope is not None and (scope.is_guest or (recipe["owner_account_id"] is None and not scope.is_admin)):
        raise HTTPException(403, "Dieses Rezept darfst du nicht bearbeiten")
    if recipe["ingredients_status"] == "running":
        raise HTTPException(409, "Die Zutaten werden gerade neu eingelesen. Bitte danach erneut prüfen.")
    ingredients = [dict(item) for item in connection.execute(
        "SELECT name,canonical_name,amount,unit,raw,sort_order FROM recipe_ingredients "
        "WHERE recipe_id=? ORDER BY sort_order,id LIMIT 201", (recipe_id,))]
    steps = [dict(item) for item in connection.execute(
        "SELECT step_number,instruction,timer_seconds FROM recipe_steps "
        "WHERE recipe_id=? ORDER BY step_number,id LIMIT 301", (recipe_id,))]
    snapshot = {"recipe": recipe, "ingredients": ingredients, "steps": steps}
    try:
        encoded = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "Die gespeicherten Mengen sind ungültig. Bitte zuerst korrigieren.") from exc
    if len(ingredients) > 200 or len(steps) > 300 or len(encoded) > 40_000:
        raise HTTPException(422, "Dieses Rezept ist zu umfangreich für die Portionsprüfung.")
    return snapshot, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def estimate_preview(db, recipe_id, analyzer_factory):
    with db.conn() as connection:
        connection.execute("BEGIN")
        snapshot, fingerprint = _snapshot(connection, db, recipe_id)
    recipe, ingredients = snapshot["recipe"], snapshot["ingredients"]
    response = {"current_servings": recipe["servings"], "fingerprint": fingerprint}
    quantified = [item for item in ingredients
                  if isinstance(item["amount"], (int, float)) and not isinstance(item["amount"], bool)
                  and math.isfinite(item["amount"]) and item["amount"] > 0
                  and bool(str(item["name"] or "").strip())]
    seasoning_only = {"salz", "pfeffer", "wasser", "muskat", "muskatnuss", "zimt", "oregano", "basilikum"}
    substantial = [item for item in quantified if canonical_name(item["name"]) not in seasoning_only]
    if not substantial:
        return {**response, "suggested_servings": None, "min_servings": None, "max_servings": None,
                "confidence": "low", "rationale": "Zu wenige Zutaten haben verwertbare Mengenangaben. Bitte zuerst die Mengen ergänzen.",
                "warnings": ["Es wurde keine Portionszahl geschätzt und keine KI-Anfrage gesendet."]}
    payload = {key: recipe[key] for key in ("name", "type", "category", "description")}
    payload["current_servings"] = recipe["servings"]
    payload["ingredients"] = [{key: item[key] for key in ("name", "amount", "unit")} for item in ingredients]
    payload["steps"] = [item["instruction"] for item in snapshot["steps"]]
    result = analyzer_factory().estimate_servings(payload)
    try:
        result = EstimateResult.model_validate(result).model_dump()
    except ValidationError as exc:
        raise HTTPException(502, "Die KI hat keine verlässliche Portionsprüfung geliefert. Bitte erneut versuchen.") from exc
    result["warnings"].append("Portionsgrößen unterscheiden sich nach Appetit und Gerichtart. Dies ist eine Schätzung.")
    if len(quantified) < len(ingredients):
        result["warnings"].append("Nicht alle Zutaten haben Mengenangaben; fehlende Hauptzutaten können die Schätzung verändern.")
    return {**response, **result}


def apply_estimate(db, recipe_id, servings, fingerprint, actor):
    with db.conn() as connection:
        connection.execute("BEGIN IMMEDIATE")
        snapshot, current_fingerprint = _snapshot(connection, db, recipe_id)
        if current_fingerprint != fingerprint:
            raise HTTPException(409, "Das Rezept wurde seit der Prüfung geändert. Bitte die Portionszahl erneut prüfen.")
        if snapshot["recipe"]["servings"] == servings:
            return {"ok": True, "servings": servings}
        full_snapshot = db._recipe_snapshot_from_connection(connection, recipe_id)
        version_no = connection.execute(
            "SELECT COALESCE(MAX(version_no),0)+1 FROM recipe_versions WHERE recipe_id=?", (recipe_id,),
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO recipe_versions(recipe_id,version_no,created_at,created_by,source,reason,snapshot_json) "
            "VALUES(?,?,?,?,?,?,?)",
            (recipe_id, version_no, time.time(), actor, "ai", "KI-Portionsvorschlag übernommen",
             json.dumps(full_snapshot, ensure_ascii=False, separators=(",", ":"))),
        )
        connection.execute(
            "DELETE FROM recipe_versions WHERE recipe_id=? AND id NOT IN "
            "(SELECT id FROM recipe_versions WHERE recipe_id=? ORDER BY version_no DESC LIMIT 50)",
            (recipe_id, recipe_id),
        )
        # Amounts and ingredient totals stay untouched; only per-serving values
        # and their publishing lease become stale when the divisor changes.
        connection.execute(
            "UPDATE recipes SET servings=?,calories_per_serving=NULL,protein_g=NULL,carbs_g=NULL,fat_g=NULL,"
            "nutrition_computed_at=NULL,nutrition_claimed_at=NULL,nutrition_claim_owner=NULL WHERE id=?",
            (servings, recipe_id),
        )
    return {"ok": True, "servings": servings}
