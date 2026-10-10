"""Ingredient matching and constrained suggestions for the existing collection."""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, StringConstraints, model_validator

from ..auth import require_auth
from ..db import get_db
from ..recipes.discovery import add_missing_to_cart, ingredient_matches, meal_plan_suggestions

router = APIRouter(prefix="/api/discovery", tags=["discovery"], dependencies=[Depends(require_auth)])
IngredientName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
RecipeId = Annotated[int, Field(gt=0)]


class IngredientRequest(BaseModel):
    ingredients: list[IngredientName] = Field(min_length=1, max_length=100)
    limit: int = Field(default=20, ge=1, le=50)


class MealPlanRequest(BaseModel):
    count: int = Field(default=4, ge=1, le=7)
    vegetarian_count: int = Field(default=0, ge=0, le=7)
    max_minutes: int | None = Field(default=None, ge=1, le=1440)
    exclude_recipe_ids: list[RecipeId] = Field(default_factory=list, max_length=500)
    available_ingredients: list[IngredientName] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_counts(self):
        if self.vegetarian_count > self.count:
            raise ValueError("Vegetarische Gerichte dürfen die Gesamtzahl nicht überschreiten")
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
