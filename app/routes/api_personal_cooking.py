"""Account-scoped cooking memory and permission-preserving import review."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .. import auth
from ..db import get_db
from ..recipes import personal_cooking as service
from ..recipes.canonical import canonical_name
from ..recipes.units import normalize_unit

router = APIRouter(
    prefix="/api/recipes", tags=["personal-cooking"], dependencies=[Depends(auth.require_auth)]
)

ClientID = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")
]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]


async def _identity(request: Request, response: Response) -> str:
    # Private notes must never enter intermediary or browser HTTP caches.
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Authorization, Cookie"
    if auth.request_is_guest(request):
        raise HTTPException(403, "Persönliche Funktionen benötigen ein Benutzerkonto.")
    username = auth.request_user(request)
    if not username:
        raise HTTPException(401, "Authentication required")
    return username


async def _can_apply(request: Request) -> bool:
    try:
        await auth.require_admin(request)
        return True
    except HTTPException as exc:
        if exc.status_code != 403:
            raise
        return False


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except service.RevisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except service.EntryDeleted as exc:
        raise HTTPException(410, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class MemoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_entry_id: ClientID
    note: ShortText = ""
    adjustments: ShortText = ""
    next_time: ShortText = ""
    servings: int | None = Field(None, ge=1, le=50)
    step_number: int | None = Field(None, ge=1, le=200)
    step_instruction: str | None = Field(None, max_length=10000)

    @model_validator(mode="after")
    def validate_content(self):
        if not (self.note or self.adjustments or self.next_time):
            raise ValueError("Mindestens eine Erfahrung, Anpassung oder Idee ist erforderlich")
        if (self.step_number is None) != (self.step_instruction is None):
            raise ValueError("Schrittnummer und ursprüngliche Anweisung müssen gemeinsam angegeben werden")
        return self


@router.get("/{recipe_id}/cooking-memory")
def list_memory(
    recipe_id: int,
    username: str = Depends(_identity),
    limit: int = Query(100, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    return _call(service.memory_list, get_db(), recipe_id, username, limit=limit, offset=offset)


@router.post("/{recipe_id}/cooking-memory")
def create_memory(recipe_id: int, payload: MemoryCreate, username: str = Depends(_identity)):
    return {
        "ok": True,
        "entry": _call(service.memory_create, get_db(), recipe_id, username, payload.model_dump()),
    }


@router.delete("/{recipe_id}/cooking-memory/{entry_id}")
def delete_memory(recipe_id: int, entry_id: int, username: str = Depends(_identity)):
    _call(service.memory_delete, get_db(), recipe_id, username, entry_id)
    return {"ok": True}


@router.delete("/{recipe_id}/cooking-memory/client/{client_entry_id}")
def cancel_memory(recipe_id: int, client_entry_id: ClientID, username: str = Depends(_identity)):
    _call(service.memory_cancel, get_db(), recipe_id, username, client_entry_id)
    return {"ok": True}


class ReviewIngredient(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
    amount: float | None = Field(None, ge=0, le=1000000, allow_inf_nan=False)
    unit: Annotated[str, StringConstraints(strip_whitespace=True, max_length=50)] | None = None
    raw: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] | None = None


class ReviewStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instruction: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10000)]
    timer_seconds: int | None = Field(None, ge=1, le=86400)


class ImportCorrectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_request_id: ClientID
    expected_revision: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    ingredients: list[ReviewIngredient] = Field(min_length=1, max_length=200)
    steps: list[ReviewStep] = Field(min_length=1, max_length=200)
    servings: int | None = Field(None, ge=1, le=50)
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]

    def prepared(self) -> dict:
        result = self.model_dump()
        for item in result["ingredients"]:
            item["name"] = " ".join(item["name"].split())
            item["canonical_name"] = canonical_name(item["name"])
            item["unit"] = normalize_unit(item["unit"])
        for index, step in enumerate(result["steps"], 1):
            step["step_number"] = index
        return result


@router.get("/{recipe_id}/import-review")
def get_import_review(
    recipe_id: int, username: str = Depends(_identity), can_apply: bool = Depends(_can_apply)
):
    return _call(service.import_review, get_db(), recipe_id, username, can_apply)


@router.post("/{recipe_id}/import-review")
def submit_import_review(
    recipe_id: int,
    payload: ImportCorrectionCreate,
    username: str = Depends(_identity),
    can_apply: bool = Depends(_can_apply),
):
    db = get_db()
    correction = _call(service.submit_correction, db, recipe_id, username, can_apply, payload.prepared())
    if correction["status"] == "applied":
        from .api_recipes import _FACET_CACHE

        _FACET_CACHE.clear()
    return {
        "ok": True,
        "status": correction["status"],
        "correction": correction,
        "review": _call(service.import_review, db, recipe_id, username, can_apply),
    }


@router.post("/{recipe_id}/import-review/{correction_id}/apply", dependencies=[Depends(auth.require_admin)])
def apply_import_review(recipe_id: int, correction_id: int, username: str = Depends(_identity)):
    db = get_db()
    correction = _call(service.apply_correction, db, recipe_id, correction_id, username)
    from .api_recipes import _FACET_CACHE

    _FACET_CACHE.clear()
    return {
        "ok": True,
        "status": correction["status"],
        "correction": correction,
        "review": _call(service.import_review, db, recipe_id, username, True),
    }


@router.delete("/{recipe_id}/import-review/{correction_id}")
def withdraw_import_review(
    recipe_id: int,
    correction_id: int,
    username: str = Depends(_identity),
    can_apply: bool = Depends(_can_apply),
):
    correction = _call(service.withdraw_correction, get_db(), recipe_id, correction_id, username, can_apply)
    return {"ok": True, "correction": correction}
