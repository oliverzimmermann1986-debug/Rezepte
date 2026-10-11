"""Ingredient matching and constrained suggestions for the existing collection."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

from ..auth import require_auth
from ..db import get_db
from ..recipes.canonical import canonical_name
from ..recipes.discovery import add_missing_to_cart, ingredient_matches, meal_plan_suggestions

router = APIRouter(prefix="/api/discovery", tags=["discovery"], dependencies=[Depends(require_auth)])
IngredientName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
RecipeId = Annotated[int, Field(gt=0)]


class IngredientRequest(BaseModel):
    ingredients: list[IngredientName] = Field(min_length=1, max_length=100)
    limit: int = Field(default=20, ge=1, le=50)


class CategoryQuota(BaseModel):
    category: Literal["pasta", "potato", "rice", "legume"]
    count: int = Field(ge=0, le=7)


class WeekdayRule(BaseModel):
    weekday: int = Field(ge=1, le=7)
    category: Literal["pasta", "potato", "rice", "legume"] | None = None
    min_minutes: int | None = Field(default=None, ge=1, le=1440)
    max_minutes: int | None = Field(default=None, ge=1, le=1440)
    vegetarian: bool = False

    @model_validator(mode="after")
    def validate_time_range(self):
        if self.min_minutes is not None and self.max_minutes is not None and self.min_minutes > self.max_minutes:
            raise ValueError("Die Mindestzeit darf die Höchstzeit nicht überschreiten")
        return self


class MealPlanRequest(BaseModel):
    count: int = Field(default=4, ge=1, le=7)
    vegetarian_count: int = Field(default=0, ge=0, le=7)
    max_minutes: int | None = Field(default=None, ge=1, le=1440)
    exclude_recipe_ids: list[RecipeId] = Field(default_factory=list, max_length=500)
    available_ingredients: list[IngredientName] = Field(default_factory=list, max_length=100)
    category_quotas: list[CategoryQuota] = Field(default_factory=list, max_length=4)
    excluded_ingredients: list[IngredientName] = Field(default_factory=list, max_length=100)
    avoid_recent_days: Literal[0, 7, 14, 28] = 0
    week_start: date | None = None
    weekday_rules: list[WeekdayRule] = Field(default_factory=list, max_length=7)

    @field_validator("week_start", mode="before")
    @classmethod
    def validate_week_start(cls, value):
        if value is not None and (not isinstance(value, str) or len(value) != 10):
            raise ValueError("week_start muss ein Datum YYYY-MM-DD sein")
        return value

    @model_validator(mode="after")
    def validate_counts(self):
        if self.vegetarian_count > self.count:
            raise ValueError("Vegetarische Gerichte dürfen die Gesamtzahl nicht überschreiten")
        if any(quota.count > self.count for quota in self.category_quotas):
            raise ValueError("Eine Kategoriequote darf die Gesamtzahl nicht überschreiten")
        if len({quota.category for quota in self.category_quotas}) != len(self.category_quotas):
            raise ValueError("Jede Kategorie darf nur einmal vorkommen")
        if any(not canonical_name(name) for name in self.excluded_ingredients):
            raise ValueError("Zutatenausschlüsse müssen einen erkennbaren Namen enthalten")
        if len(self.weekday_rules) > self.count:
            raise ValueError("Es dürfen nicht mehr Tagesregeln als Gerichte gewählt werden")
        if len({rule.weekday for rule in self.weekday_rules}) != len(self.weekday_rules):
            raise ValueError("Jeder Wochentag darf nur einmal vorkommen")
        if self.weekday_rules and (self.week_start is None or self.week_start.isoweekday() != 1):
            raise ValueError("Für Tagesregeln muss week_start der Montag der gewählten Woche sein")
        if self.week_start is not None:
            try:
                self.week_start - timedelta(days=self.avoid_recent_days)
                if self.weekday_rules:
                    self.week_start + timedelta(days=6)
            except OverflowError as exc:
                raise ValueError("Die Woche oder der Rückblick liegt außerhalb des gültigen Datumsbereichs") from exc
        return self


class MissingCartRequest(BaseModel):
    request_id: UUID
    recipe_id: int = Field(gt=0)
    # The same AVAILABLE ingredients used in discovery, never client-supplied missing rows.
    ingredients: list[IngredientName] = Field(default_factory=list, max_length=100)
    servings: int | None = Field(default=None, ge=1, le=50)


@router.post("/ingredients")
def discover_ingredients(payload: IngredientRequest):
    return ingredient_matches(get_db(), payload.ingredients, payload.limit)


@router.post("/meal-plan")
def discover_meal_plan(payload: MealPlanRequest):
    return meal_plan_suggestions(get_db(), **payload.model_dump())


@router.post("/missing-to-cart")
def missing_to_cart(payload: MissingCartRequest):
    try:
        return add_missing_to_cart(get_db(), payload.recipe_id, payload.ingredients, payload.servings,
                                   request_id=str(payload.request_id))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
