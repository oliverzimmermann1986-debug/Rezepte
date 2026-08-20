"""Wochenplan mit automatisch aggregierter Einkaufsliste."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from ..auth import require_auth
from ..db import get_db
from ..recipes.cart_logic import (
    aggregate_recipes_for_cart,
    aggregated_cart_for_display,
)
from ..recipes.meal_plan_pdf import build_meal_plan_pdf
from .api_einkauf import einkauf_configured, einkauf_request

router = APIRouter(
    prefix="/api/meal-plan",
    tags=["meal-plan"],
    dependencies=[Depends(require_auth)],
)

_DAY_NAMES = [
    "Montag",
    "Dienstag",
    "Mittwoch",
    "Donnerstag",
    "Freitag",
    "Samstag",
    "Sonntag",
]


def _parse_date(value: Optional[str], *, field: str = "Datum") -> date:
    if not value:
        return date.today()
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(422, f"{field} muss YYYY-MM-DD sein") from exc


def _monday(value: Optional[str]) -> date:
    selected = _parse_date(value, field="week_start")
    return selected - timedelta(days=selected.weekday())


def _entry_multiplier(entry: dict) -> float:
    base_servings = entry.get("recipe_servings")
    planned_servings = entry.get("planned_servings")
    try:
        if base_servings and float(base_servings) > 0:
            return float(planned_servings) / float(base_servings)
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    return 1.0


def _week_payload(week_start: date) -> dict:
    db = get_db()
    week_end = week_start + timedelta(days=6)
    entries = db.meal_plan_entries(week_start.isoformat(), week_end.isoformat())
    by_day: dict[str, list[dict]] = {}
    selections = []
    for entry in entries:
        multiplier = _entry_multiplier(entry)
        item = {
            **entry,
            "multiplier": round(multiplier, 4),
            "scalable": bool(entry.get("recipe_servings")),
            "thumb_url": f"/api/recipes/{entry['recipe_id']}/thumb",
        }
        by_day.setdefault(entry["planned_for"], []).append(item)
        selections.append({
            "recipe_id": entry["recipe_id"],
            "multiplier": multiplier,
        })

    aggregated = aggregate_recipes_for_cart(db, selections)
    preview = aggregated_cart_for_display(aggregated)
    today = date.today()
    days = []
    for offset, label in enumerate(_DAY_NAMES):
        current = week_start + timedelta(days=offset)
        days.append({
            "date": current.isoformat(),
            "label": label,
            "short_label": label[:2],
            "day_number": current.day,
            "is_today": current == today,
            "items": by_day.get(current.isoformat(), []),
        })

    return {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "previous_week": (week_start - timedelta(days=7)).isoformat(),
        "next_week": (week_start + timedelta(days=7)).isoformat(),
        "is_current_week": week_start <= today <= week_end,
        "days": days,
        "shopping_preview": preview,
        "summary": {
            "planned_meals": len(entries),
            "planned_days": len(by_day),
            "shopping_items": len(preview),
        },
    }


@router.get("")
def get_week(week_start: Optional[str] = Query(None)):
    return _week_payload(_monday(week_start))


@router.get("/pdf")
def get_week_pdf(week_start: Optional[str] = Query(None)):
    week = _week_payload(_monday(week_start))
    try:
        pdf = build_meal_plan_pdf(week)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    filename = f"wochenplan-{week['week_start']}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


class MealPlanCreate(BaseModel):
    planned_for: str
    recipe_id: int = Field(gt=0)
    planned_servings: int = Field(default=2, ge=1, le=24)


@router.post("/items")
def add_item(payload: MealPlanCreate):
    planned_for = _parse_date(payload.planned_for, field="planned_for")
    try:
        item = get_db().meal_plan_add(
            planned_for=planned_for.isoformat(),
            recipe_id=payload.recipe_id,
            planned_servings=payload.planned_servings,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True, "item": item}


class MealPlanUpdate(BaseModel):
    planned_for: Optional[str] = None
    planned_servings: Optional[int] = Field(default=None, ge=1, le=24)


@router.patch("/items/{item_id}")
def update_item(item_id: int, payload: MealPlanUpdate):
    planned_for = None
    if payload.planned_for is not None:
        planned_for = _parse_date(
            payload.planned_for,
            field="planned_for",
        ).isoformat()
    try:
        updated = get_db().meal_plan_update(
            item_id,
            planned_for=planned_for,
            planned_servings=payload.planned_servings,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not updated:
        raise HTTPException(404, "Wochenplan-Eintrag nicht gefunden")
    return {"ok": True}


@router.delete("/items/{item_id}")
def delete_item(item_id: int):
    if not get_db().meal_plan_delete(item_id):
        raise HTTPException(404, "Wochenplan-Eintrag nicht gefunden")
    return {"ok": True}


class MealPlanCartPayload(BaseModel):
    week_start: Optional[str] = None


def _format_shopping_line(item: dict) -> str:
    parts = []
    amount = item.get("amount")
    if amount is not None:
        amount_value = float(amount)
        parts.append(
            str(int(amount_value))
            if amount_value.is_integer()
            else f"{amount_value:.2f}".rstrip("0").rstrip(".")
        )
    if item.get("unit"):
        parts.append(str(item["unit"]))
    parts.append(str(item.get("name") or "?"))
    return " ".join(parts)[:150]


@router.post("/cart")
def create_week_cart(payload: MealPlanCartPayload):
    week = _week_payload(_monday(payload.week_start))
    preview = week["shopping_preview"]
    if not preview:
        raise HTTPException(400, "Für diese Woche sind keine einkaufbaren Zutaten geplant")

    if einkauf_configured():
        lines = [_format_shopping_line(item) for item in preview]
        einkauf_request(
            "POST",
            "/items/bulk",
            {"items": lines, "source": "wochenplan"},
        )
        einkauf_request("POST", "/consolidate")
        return {
            "ok": True,
            "target": "einkauf",
            "added": len(lines),
            "week_start": week["week_start"],
        }

    base_items = [
        {
            **item,
            "amount": item.get("amount_base"),
            "unit": item.get("unit_base"),
        }
        for item in preview
    ]
    added = get_db().cart_replace(base_items)
    return {
        "ok": True,
        "target": "local",
        "added": added,
        "replaced": True,
        "week_start": week["week_start"],
    }
