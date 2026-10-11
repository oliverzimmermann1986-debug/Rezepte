"""Private PDF export of the selected suggestion snapshot, without replanning."""
from __future__ import annotations

from datetime import date, timedelta
import threading

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..auth import require_auth
from ..db import get_db
from .api_discovery import MealPlanRequest

router = APIRouter(prefix="/api/discovery", tags=["discovery"], dependencies=[Depends(require_auth)])
_pdf_slots = threading.BoundedSemaphore(2)


class MealPlanPDFItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipe_id: int = Field(gt=0, strict=True)
    date: date
    servings: int = Field(ge=1, le=24, strict=True)

    @field_validator("date", mode="before")
    @classmethod
    def validate_date(cls, value):
        if not isinstance(value, str) or len(value) != 10:
            raise ValueError("date muss ein Datum YYYY-MM-DD sein")
        return value


class MealPlanPDFRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criteria: MealPlanRequest
    items: list[MealPlanPDFItem] = Field(min_length=1, max_length=7)

    @model_validator(mode="after")
    def validate_snapshot(self):
        anchor = self.criteria.week_start
        if anchor is None or anchor.weekday() != 0:
            raise ValueError("Für den PDF-Export ist week_start als Montag erforderlich")
        try:
            end = anchor + timedelta(days=6)
        except OverflowError as exc:
            raise ValueError("Die Woche liegt außerhalb des gültigen Datumsbereichs") from exc
        if any(not anchor <= item.date <= end for item in self.items):
            raise ValueError("Alle Vorschläge müssen innerhalb der gewählten Woche liegen")
        if len({item.recipe_id for item in self.items}) != len(self.items):
            raise ValueError("Jedes vorgeschlagene Rezept darf nur einmal vorkommen")
        return self


@router.post("/meal-plan/pdf")
def meal_plan_pdf(payload: MealPlanPDFRequest):
    from ..recipes.meal_suggestion_pdf import build_meal_plan_pdf, meal_plan_snapshot

    db = get_db()
    if getattr(getattr(db, "scope", None), "is_guest", False):
        raise HTTPException(403, "Wochenvorschläge sind für angemeldete Haushalte verfügbar.")
    if not _pdf_slots.acquire(blocking=False):
        raise HTTPException(429, "Andere Wochenvorschläge werden gerade exportiert. Bitte kurz warten.",
                            headers={"Retry-After": "5"})
    try:
        criteria = payload.criteria.model_dump()
        snapshot = meal_plan_snapshot(db, criteria, [item.model_dump() for item in payload.items])
        pdf = build_meal_plan_pdf(snapshot)
        return Response(pdf, media_type="application/pdf", headers={
            "Content-Disposition": f'attachment; filename="wochenvorschlaege-{payload.criteria.week_start.isoformat()}.pdf"',
            "Cache-Control": "private, no-store", "Vary": "Authorization, Cookie",
            "X-Content-Type-Options": "nosniff",
        })
    finally:
        _pdf_slots.release()
