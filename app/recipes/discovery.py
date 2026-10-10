"""Deterministic suggestions from the caller's visible recipe collection."""
from __future__ import annotations

import math
import re

from .canonical import canonical_name
from .cart_logic import prepare_for_cart


AVAILABILITY_NOTE = "Es werden vorhandene Zutaten verglichen, keine Vorratsmengen. Mengen bitte vor dem Kochen prüfen."
_TIME_LINE = re.compile(
    r"^\s*(?:gesamtzeit|gesamtdauer|total\s+(?:time|duration))\s*:\s*(.*?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_DURATION = re.compile(
    r"(?:(\d+(?:[.,]\d+)?)\s*(?:stunden?|std\.?|h)\s*)?"
    r"(?:(\d+(?:[.,]\d+)?)\s*(?:minuten?|min\.?|m)\s*)?",
    re.IGNORECASE,
)


def available_keys(ingredients: list[str]) -> set[str]:
    return {key for name in ingredients if (key := canonical_name(name))}


def _key(ingredient: dict) -> str | None:
    return canonical_name(ingredient.get("canonical_name") or ingredient.get("name"))


def _amount(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError):
        return None


def explicit_total_minutes(recipe: dict) -> int | None:
    """Only a labelled total duration is evidence; step timers omit prep/rest."""
    values = []
    for line in _TIME_LINE.findall(recipe.get("description") or ""):
        duration = _DURATION.fullmatch(line.strip())
        if not duration or not any(duration.groups()):
            return None
        hours, minutes = (float(value.replace(",", ".")) if value else 0 for value in duration.groups())
        total = hours * 60 + minutes
        if not math.isfinite(total) or total <= 0:
            return None
        values.append(math.ceil(total))
    # Conflicting source declarations cannot establish a maximum duration.
    return values[0] if values and len(set(values)) == 1 else None


def _complete_ingredients(ingredients: list[dict]) -> bool:
    return bool(ingredients) and all(str(item.get("name") or "").strip() and _key(item) for item in ingredients)


def _candidates(db):
    """Use the existing visibility/deletion filters, including library selection."""
    offset = 0
    while True:
        recipes = db.recipe_list(ingredients_status="ok", needs_manual_care=False, limit=400, offset=offset)
        if not recipes:
            break
        ingredients_by_id = db.recipe_ingredients_for_ids([int(recipe["id"]) for recipe in recipes])
        for recipe in recipes:
            ingredients = ingredients_by_id.get(int(recipe["id"]), [])
            if _complete_ingredients(ingredients):
                yield recipe, ingredients
        offset += len(recipes)
        if len(recipes) < 400:
            break


def ingredient_matches(db, ingredients: list[str], limit: int = 20) -> dict:
    available = available_keys(ingredients)
    if not available:
        return {"items": [], "warnings": ["Bitte mindestens eine vorhandene Zutat eingeben.", AVAILABILITY_NOTE]}
    items = []
    for recipe, recipe_ingredients in _candidates(db):
        all_keys = {_key(item) for item in recipe_ingredients}
        matched_keys = all_keys & available
        if not matched_keys:
            continue
        matched_names = {}
        missing = []
        for item in recipe_ingredients:
            if _key(item) in available:
                matched_names.setdefault(_key(item), item["name"])
            else:
                missing.append({"name": item["name"], "amount": _amount(item.get("amount")), "unit": item.get("unit")})
        items.append({
            "recipe_id": int(recipe["id"]), "name": recipe["name"],
            "thumb_url": f"/api/recipes/{recipe['id']}/thumb",
            "matched_ingredients": list(matched_names.values()), "missing_ingredients": missing,
            "coverage": len(matched_keys) / len(all_keys),
        })
    items.sort(key=lambda item: (-item["coverage"], len(item["missing_ingredients"]), item["name"].casefold(), item["recipe_id"]))
    return {"items": items[:limit], "warnings": [AVAILABILITY_NOTE]}


def meal_plan_suggestions(db, *, count: int, vegetarian_count: int = 0,
                          max_minutes: int | None = None, exclude_recipe_ids: list[int] | None = None,
                          available_ingredients: list[str] | None = None) -> dict:
    """Reserve vegetarian slots, then fill open slots, without relaxing constraints."""
    available = available_keys(available_ingredients or [])
    excluded = set(exclude_recipe_ids or [])
    candidates = []
    unknown_times = 0
    for recipe, ingredients in _candidates(db):
        if int(recipe["id"]) in excluded:
            continue
        total = explicit_total_minutes(recipe)
        if max_minutes is not None and total is None:
            unknown_times += 1
            continue
        if max_minutes is not None and total > max_minutes:
            continue
        tags = {str(tag["name"]).strip().casefold() for tag in db.recipe_tags_get(recipe["id"])}
        keys = {_key(item) for item in ingredients}
        servings = _amount(recipe.get("servings"))
        candidates.append({
            "recipe_id": int(recipe["id"]), "name": recipe["name"],
            "thumb_url": f"/api/recipes/{recipe['id']}/thumb", "total_minutes": total,
            "vegetarian": bool(tags & {"vegetarisch", "vegan"}),
            "servings": max(1, min(24, int(servings))) if servings and servings.is_integer() else 2,
            "_keys": keys, "_category": str(recipe.get("category") or "").casefold(),
            "_coverage": len(keys & available) / len(keys) if available else 0,
        })
    selected = []

    def choose(vegetarian_only=False):
        eligible = [item for item in candidates if not vegetarian_only or item["vegetarian"]]
        if not eligible:
            return False
        categories = {item["_category"] for item in selected if item["_category"]}
        used_keys = set().union(*(item["_keys"] for item in selected)) if selected else set()
        eligible.sort(key=lambda item: (
            -item["_coverage"], item["_category"] in categories,
            len(item["_keys"] & used_keys) / len(item["_keys"]),
            item["name"].casefold(), item["recipe_id"],
        ))
        choice = eligible[0]
        candidates.remove(choice)
        selected.append(choice)
        return True

    for _ in range(vegetarian_count):
        if not choose(vegetarian_only=True):
            break
    vegetarian_found = len(selected)
    # Missing vegetarian slots stay empty; never replace them with other meals.
    for _ in range(count - vegetarian_count):
        if not choose():
            break
    warnings = []
    if vegetarian_found < vegetarian_count:
        warnings.append(f"Nur {vegetarian_found} von {vegetarian_count} benötigten vegetarisch oder vegan markierten Rezepten gefunden.")
    if len(selected) < count:
        warnings.append(f"Nur {len(selected)} von {count} passenden unterschiedlichen Rezepten gefunden. Die Vorgaben wurden beibehalten.")
    if unknown_times:
        warnings.append(f"{unknown_times} Rezepte ohne eindeutige Gesamtzeit wurden wegen der Zeitgrenze ausgelassen.")
    if vegetarian_count:
        warnings.append("Vegetarisch richtet sich nach den gespeicherten Rezept-Tags; bitte Zutaten bei Bedarf prüfen.")
    return {"items": [{key: value for key, value in item.items() if not key.startswith("_")} for item in selected], "warnings": warnings}


def add_missing_to_cart(db, recipe_id: int, available: list[str], servings: int | None = None,
                        *, request_id: str) -> dict:
    keys = available_keys(available)
    request_payload = {"recipe_id": recipe_id, "ingredients": sorted(keys), "servings": servings}
    previous = db.discovery_cart_receipt(request_id, request_payload)
    if previous is not None:
        return previous
    recipe = db.recipe_get(recipe_id)
    if not recipe or recipe.get("deleted_at") is not None:
        raise LookupError("Rezept nicht gefunden")
    ingredients = db.recipe_ingredients_get(recipe_id)
    if recipe.get("ingredients_status") != "ok" or not _complete_ingredients(ingredients) or not db.recipe_steps_get(recipe_id):
        raise ValueError("Bitte zuerst Zutaten und Zubereitung des Rezepts vervollständigen")
    multiplier = 1.0
    if servings is not None:
        original_servings = _amount(recipe.get("servings"))
        if not original_servings:
            raise ValueError("Portionszahl im Rezept fehlt; bitte zuerst im Rezept ergänzen")
        multiplier = servings / original_servings
    excluded = {canonical_name(value) for value in db.shopping_excluded_canonicals()}
    items = []
    skipped = 0
    for ingredient in ingredients:
        if _key(ingredient) in keys or _key(ingredient) in excluded:
            skipped += 1
            continue
        amount = _amount(ingredient.get("amount"))
        # Preserve overflow for the database's finite-quantity guard. Converting
        # it into None would silently erase a known ingredient quantity.
        prepared = prepare_for_cart(ingredient["name"], amount * multiplier if amount is not None else None, ingredient.get("unit"))
        from .shopping_history import contribution
        items.append({**prepared, "source_recipe_ids": [recipe_id],
                      "source_contributions": [contribution(recipe_id, prepared["amount"], prepared["unit"])]})
    return db.discovery_cart_merge(request_id, request_payload, items,
                                   {"skipped": skipped, "target": "local", "multiplier": multiplier})
