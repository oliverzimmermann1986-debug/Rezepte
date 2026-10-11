"""Private PDF export of the selected suggestion snapshot, without replanning."""
from __future__ import annotations

from datetime import date, timedelta
import threading

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..auth import require_auth
from ..db import get_db
from .api_discovery import MealPlanRequest
from typing import Literal

router = APIRouter(prefix="/api/discovery", tags=["discovery"], dependencies=[Depends(require_auth)])
_pdf_slots = threading.BoundedSemaphore(2)


class MealPlanPDFItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipe_id: int = Field(gt=0, strict=True)
    date: date
    servings: int = Field(ge=1, le=24, strict=True)
    meal_course: Literal["breakfast", "starter", "dessert"] | None = None

    @field_validator("date", mode="before")
    @classmethod
    def validate_date(cls, value):
        if not isinstance(value, str) or len(value) != 10:
            raise ValueError("date muss ein Datum YYYY-MM-DD sein")
        return value


class ExtraCourseSlot(BaseModel):
    weekday: int = Field(ge=1, le=7)
    course: Literal["breakfast", "starter", "dessert"]


class MealPlanPDFCriteria(MealPlanRequest):
    additional_courses: list[ExtraCourseSlot] = Field(default_factory=list, max_length=21)

    @model_validator(mode="after")
    def validate_courses(self):
        if len({(slot.weekday, slot.course) for slot in self.additional_courses}) != len(self.additional_courses):
            raise ValueError("Jede zusätzliche Mahlzeit darf pro Tag nur einmal vorkommen")
        return self


class MealPlanPDFRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criteria: MealPlanPDFCriteria
    items: list[MealPlanPDFItem] = Field(min_length=1, max_length=28)

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
        if sum(item.meal_course is None for item in self.items) > 7:
            raise ValueError("Es dürfen höchstens sieben Hauptgerichte exportiert werden")
        extras = [(item.date.isoweekday(), item.meal_course) for item in self.items if item.meal_course]
        requested = {(slot.weekday, slot.course) for slot in self.criteria.additional_courses}
        if len(set(extras)) != len(extras) or not set(extras) <= requested:
            raise ValueError("Zusätzliche Mahlzeiten müssen zu den optionalen Tagesvorgaben passen")
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
