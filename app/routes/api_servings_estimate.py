"""Explicit AI preview followed by a separate, local-only portions update."""
import threading

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..ai_consent import require_ai_consent
from ..auth import require_auth, request_user
from ..config_store import get_config
from ..core.analyzer import build_analyzer
from ..db import get_db
from ..recipes.servings_estimate import Portions, apply_estimate, estimate_preview
from ..tenancy import require_recipe_editor

router = APIRouter(prefix="/api/recipes", tags=["recipes"],
                   dependencies=[Depends(require_auth), Depends(require_recipe_editor)])
_estimate_slots = threading.BoundedSemaphore(2)


class ServingsEstimateApply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    servings: Portions
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


def _analyzer():
    try:
        return build_analyzer(get_config().get("ai", default={}) or {})
    except (TypeError, ValueError) as exc:
        raise HTTPException(503, "Die KI ist auf diesem Server noch nicht verfügbar. Bitte die KI-Einstellungen prüfen.") from exc


@router.post("/{recipe_id}/servings-estimate", dependencies=[Depends(require_ai_consent)])
def servings_estimate(recipe_id: int):
    if not _estimate_slots.acquire(blocking=False):
        raise HTTPException(429, "Andere Portionsprüfungen laufen gerade. Bitte kurz warten.", headers={"Retry-After": "5"})
    try:
        return estimate_preview(get_db(), recipe_id, _analyzer)
    finally:
        _estimate_slots.release()


@router.post("/{recipe_id}/servings-estimate/apply")
def servings_estimate_apply(recipe_id: int, payload: ServingsEstimateApply, request: Request):
    return apply_estimate(get_db(), recipe_id, payload.servings, payload.fingerprint, request_user(request) or "unknown")
