"""Authenticated household collections, cooking memories and weekly voting."""
from __future__ import annotations

from typing import Annotated
from pathlib import Path
import re
import threading

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ..auth import request_user, require_auth
from ..db import get_db
from ..recipes import household_features as features

router = APIRouter(prefix="/api", tags=["household-features"], dependencies=[Depends(require_auth)])
_pdf_slots = threading.BoundedSemaphore(2)


def actor_for(request, db):
    username = request_user(request)
    if not username:
        raise HTTPException(401, "Bitte anmelden")
    scope = getattr(db, "scope", None)
    # Middleware verified the stable session identity, not merely its display name.
    user_id = scope.user_id if scope else None
    if scope and not scope.is_guest and scope.account_id > 0 and user_id is None:
        raise HTTPException(401, "Bitte erneut anmelden")
    return features.Actor(username=username, key=f"user:{user_id}" if user_id is not None else "legacy:" + username.casefold(),
                          is_admin=bool(scope and scope.is_admin), is_guest=bool(scope and scope.is_guest))


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CookbookName(Payload):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class Note(Payload):
    note: str = Field(max_length=4000)


class Wish(Payload):
    week_start: str
    recipe_id: int = Field(gt=0)


class Vote(Payload):
    voted: bool


class Plan(Payload):
    planned_for: str
    planned_servings: int = Field(ge=1, le=24)


@router.get("/cookbooks")
def list_cookbooks(recipe_id: int | None = Query(None, gt=0)):
    return features.cookbooks(get_db(), recipe_id)


@router.post("/cookbooks")
def create_cookbook(payload: CookbookName, request: Request):
    db = get_db()
    return features.save_cookbook(db, actor_for(request, db), payload.name)


@router.patch("/cookbooks/{book_id}")
def rename_cookbook(book_id: int, payload: CookbookName, request: Request):
    db = get_db()
    return features.save_cookbook(db, actor_for(request, db), payload.name, book_id)


@router.delete("/cookbooks/{book_id}")
def delete_cookbook(book_id: int, request: Request):
    db = get_db()
    return features.delete_cookbook(db, actor_for(request, db), book_id)


@router.get("/cookbooks/{book_id}/recipes")
def cookbook_recipes(book_id: int):
    return features.cookbook_recipes(get_db(), book_id)


@router.get("/cookbooks/{book_id}/pdf")
def cookbook_pdf(book_id: int):
    from ..config_store import get_config
    from ..recipes.cookbook_pdf import build_cookbook_pdf, cookbook_snapshot
    # Bound rendering work per worker; no files or public links are persisted.
    if not _pdf_slots.acquire(blocking=False):
        raise HTTPException(429, "Andere Kochbücher werden gerade erstellt. Bitte kurz warten und erneut versuchen.",
                            headers={"Retry-After": "5"})
    try:
        book, recipes = cookbook_snapshot(get_db(), book_id)
        root = Path(get_config().get("paths", "recipe_dir", default="/mnt/rezepte"))
        pdf = build_cookbook_pdf(book, recipes, root)
        slug = re.sub(r"[^a-z0-9]+", "-", book["name"].lower()).strip("-")[:60] or "kochbuch"
        return Response(pdf, media_type="application/pdf", headers={
            "Content-Disposition": f'attachment; filename="{slug}.pdf"',
            "Cache-Control": "private, no-store", "Vary": "Authorization, Cookie",
            "X-Content-Type-Options": "nosniff",
        })
    finally:
        _pdf_slots.release()


@router.put("/cookbooks/{book_id}/recipes/{recipe_id}")
def add_cookbook_recipe(book_id: int, recipe_id: int, request: Request):
    db = get_db()
    return features.cookbook_member(db, actor_for(request, db), book_id, recipe_id, True)


@router.delete("/cookbooks/{book_id}/recipes/{recipe_id}")
def remove_cookbook_recipe(book_id: int, recipe_id: int, request: Request):
    db = get_db()
    return features.cookbook_member(db, actor_for(request, db), book_id, recipe_id, False)


@router.get("/cook-notes")
def list_cook_notes(request: Request, recipe_id: int = Query(gt=0)):
    db = get_db()
    return features.cook_notes(db, actor_for(request, db), recipe_id)


@router.put("/cook-notes/{history_id}")
def put_cook_note(history_id: int, payload: Note, request: Request):
    db = get_db()
    return features.save_note(db, actor_for(request, db), history_id, note=payload.note)


@router.post("/cook-notes/{history_id}/photo")
async def put_cook_photo(history_id: int, request: Request, file: UploadFile = File(...)):
    from starlette.concurrency import run_in_threadpool
    db = get_db()
    actor = actor_for(request, db)
    features.writable(actor)
    try:
        data = await file.read(8 * 1024 * 1024 + 1)
        photo = await run_in_threadpool(features.normalize_photo, data)
        return await run_in_threadpool(features.save_note, db, actor, history_id, photo=photo, change_photo=True)
    finally:
        await file.close()


@router.delete("/cook-notes/{history_id}/photo")
def delete_cook_photo(history_id: int, request: Request):
    db = get_db()
    return features.save_note(db, actor_for(request, db), history_id, photo=None, change_photo=True)


@router.get("/cook-notes/{history_id}/photo")
def get_cook_photo(history_id: int):
    return Response(features.note_photo(get_db(), history_id), media_type="image/jpeg",
                    headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/meal-wishes")
def list_meal_wishes(request: Request, week_start: str):
    db = get_db()
    return features.meal_wishes(db, actor_for(request, db), week_start)


@router.post("/meal-wishes")
def create_meal_wish(payload: Wish, request: Request):
    db = get_db()
    return features.add_wish(db, actor_for(request, db), payload.week_start, payload.recipe_id)


@router.put("/meal-wishes/{wish_id}/vote")
def vote_meal_wish(wish_id: int, payload: Vote, request: Request):
    db = get_db()
    return features.vote_wish(db, actor_for(request, db), wish_id, payload.voted)


@router.delete("/meal-wishes/{wish_id}")
def delete_meal_wish(wish_id: int, request: Request):
    db = get_db()
    return features.delete_wish(db, actor_for(request, db), wish_id)


@router.post("/meal-wishes/{wish_id}/plan")
def plan_meal_wish(wish_id: int, payload: Plan, request: Request):
    db = get_db()
    return features.plan_wish(db, actor_for(request, db), wish_id, payload.planned_for, payload.planned_servings)
