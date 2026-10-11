"""Constraint-bound suggestions with optionally seeded, weighted variety."""
from __future__ import annotations

import math
import hashlib
import random
import re
from datetime import date, timedelta

from .canonical import canonical_name
from .cart_logic import prepare_for_cart
from .meal_constraints import (
    CATEGORY_NAMES, WEEKDAY_NAMES, excluded_ingredient_match, recipe_categories,
    planned_weekdays, quota_selection, weekday_selection,
)


AVAILABILITY_NOTE = "Es werden vorhandene Zutaten verglichen, keine Vorratsmengen. Mengen bitte vor dem Kochen prüfen."
PREFERENCE_NOTE = "Favoriten und Bewertungen aus eurem Haushalt beeinflussen die Auswahl. Wochenvorgaben, Tagesregeln und der Rückblick bleiben verbindlich."
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


def _meal_preference(recipe: dict) -> tuple[int, list[str]]:
    """Read only the caller-scoped personal fields supplied by recipe_list.

    An unrated recipe stays neutral; even a favorite with one or two stars
    ranks below it. Preferences never remove an otherwise eligible recipe.
    """
    rating = recipe.get("rating")
    rating = rating if type(rating) is int and 1 <= rating <= 5 else 0
    favorite = bool(recipe.get("is_favorite"))
    score = (2 if favorite else 0) + {1: -5, 2: -3, 4: 1, 5: 2}.get(rating, 0)
    reasons = ["Favorit im Haushalt"] if favorite else []
    if rating:
        reasons.append(f"{rating} von 5 Sternen im Haushalt")
    return score, reasons


def _candidates(db, *, max_examined: int | None = None, scan_status: dict | None = None):
    """Use the existing visibility/deletion filters, including library selection."""
    offset = 0
    while True:
        limit = min(400, max_examined - offset + 1) if max_examined is not None else 400
        recipes = db.recipe_list(ingredients_status="ok", needs_manual_care=False, limit=limit, offset=offset)
        if not recipes:
            break
        ingredients_by_id = db.recipe_ingredients_for_ids([int(recipe["id"]) for recipe in recipes])
        for index, recipe in enumerate(recipes):
            if max_examined is not None and offset + index >= max_examined:
                if scan_status is not None:
                    scan_status["complete"] = False
                return
            ingredients = ingredients_by_id.get(int(recipe["id"]), [])
            if _complete_ingredients(ingredients):
                yield recipe, ingredients
        offset += len(recipes)
        if len(recipes) < limit:
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


def _meal_candidates(db, household_only, scan_status):
    from ..tenancy import RECIPE_LIBRARY
    # The archive's current library filter must not alter a planning request.
    token = RECIPE_LIBRARY.set("mine" if household_only else "all")
    try:
        if household_only and not getattr(db, "account_id", 0):
            return
        yield from _candidates(db, max_examined=10_000, scan_status=scan_status)
    finally:
        RECIPE_LIBRARY.reset(token)


def meal_plan_suggestions(db, *, count: int, vegetarian_count: int = 0,
                          max_minutes: int | None = None, exclude_recipe_ids: list[int] | None = None,
                          available_ingredients: list[str] | None = None,
                          category_quotas: list[dict] | None = None, excluded_ingredients: list[str] | None = None,
                          avoid_recent_days: int = 0, week_start: date | None = None,
                          weekday_rules: list[dict] | None = None, household_only: bool = False,
                          quota_mode: str = "exact", meal_course: str | None = None, random_seed=None) -> dict:
    """Select distinct recipes, jointly assigning rule days without relaxing constraints."""
    available = available_keys(available_ingredients or [])
    excluded = set(exclude_recipe_ids or [])
    excluded_keys = available_keys(excluded_ingredients or [])
    quotas = category_quotas or []
    rules = weekday_rules or []
    if avoid_recent_days:
        anchor = week_start or date.today()
        recent = db.meal_plan_entries((anchor - timedelta(days=avoid_recent_days)).isoformat(),
                                     (anchor - timedelta(days=1)).isoformat())
        excluded.update(int(item["recipe_id"]) for item in recent)
    candidates = []
    unknown_times = 0
    search_complete = True
    scan_status = {"complete": True}
    seen_recipe_ids = set()
    for recipe, ingredients in _meal_candidates(db, household_only, scan_status):
        recipe_id = int(recipe["id"])
        if recipe_id in seen_recipe_ids or recipe_id in excluded:
            continue
        seen_recipe_ids.add(recipe_id)
        keys = {_key(item) for item in ingredients}
        if excluded_ingredient_match(keys, excluded_keys):
            continue
        total = explicit_total_minutes(recipe)
        if max_minutes is not None and total is None:
            unknown_times += 1
            continue
        if max_minutes is not None and total > max_minutes:
            continue
        tags = {str(tag["name"]).strip().casefold() for tag in db.recipe_tags_get(recipe["id"])}
        from .meal_constraints import recipe_course
        course = recipe_course(recipe, tags)
        if meal_course is not None and course != meal_course:
            continue
        servings = _amount(recipe.get("servings"))
        preference_score, preference_reasons = _meal_preference(recipe)
        candidates.append({
            "recipe_id": int(recipe["id"]), "name": recipe["name"],
            "thumb_url": f"/api/recipes/{recipe['id']}/thumb", "total_minutes": total,
            "vegetarian": bool(tags & {"vegetarisch", "vegan"}),
            "meal_course": course,
            "categories": recipe_categories(recipe, tags, keys),
            "servings": max(1, min(24, int(servings))) if servings and servings.is_integer() else 2,
            "preference_reasons": preference_reasons, "_preference_score": preference_score,
            "_keys": keys, "_category": str(recipe.get("category") or "").casefold(),
            "_coverage": len(keys & available) / len(keys) if available else 0,
        })
    search_complete = scan_status["complete"]
    if random_seed is not None:
        for item in candidates:
            digest = hashlib.sha256((str(random_seed) + ':' + str(item['recipe_id'])).encode()).digest()
            rng = random.Random(int.from_bytes(digest, 'big'))
            # Preferences increase likelihood, not a permanent monopoly.
            # All feasibility filters and quotas remain hard rules.
            item['_selection_score'] = item['_preference_score'] / 3 - math.log(-math.log(max(1e-12, rng.random())))
    has_preferences = any(item["preference_reasons"] for item in candidates)
    selected = []

    def choose(vegetarian_only=False):
        eligible = [item for item in candidates if not vegetarian_only or item["vegetarian"]]
        if not eligible:
            return False
        categories = {item["_category"] for item in selected if item["_category"]}
        used_keys = set().union(*(item["_keys"] for item in selected)) if selected else set()
        eligible.sort(key=lambda item: (
            -item.get("_selection_score", item.get("_preference_score", 0)), -item["_coverage"], item["_category"] in categories,
            len(item["_keys"] & used_keys) / len(item["_keys"]),
            item["name"].casefold(), item["recipe_id"],
        ))
        choice = eligible[0]
        candidates.remove(choice)
        selected.append(choice)
        return True

    if rules:
        selected, solver_complete = weekday_selection(candidates, count, vegetarian_count, quotas, rules, quota_mode=quota_mode)
        search_complete = search_complete and solver_complete
        vegetarian_found = sum(item["vegetarian"] for item in selected)
    elif quotas:
        selected, solver_complete = quota_selection(candidates, count, vegetarian_count, quotas, quota_mode=quota_mode)
        search_complete = search_complete and solver_complete
        vegetarian_found = sum(item["vegetarian"] for item in selected)
    else:
        for _ in range(vegetarian_count):
            if not choose(vegetarian_only=True):
                break
        vegetarian_found = len(selected)
        # Missing vegetarian slots stay empty; never replace them with other meals.
        for _ in range(count - vegetarian_count):
            if not choose():
                break
    warnings = []
    if has_preferences:
        warnings.append(PREFERENCE_NOTE)
    if vegetarian_found < vegetarian_count:
        warnings.append(f"Nur {vegetarian_found} von {vegetarian_count} benötigten vegetarisch oder vegan markierten Rezepten gefunden.")
    if len(selected) < count:
        warnings.append(f"Nur {len(selected)} von {count} passenden unterschiedlichen Rezepten gefunden. Die Vorgaben wurden beibehalten.")
    if unknown_times:
        warnings.append(f"{unknown_times} Rezepte ohne eindeutige Gesamtzeit wurden wegen der Zeitgrenze ausgelassen.")
    if vegetarian_count or any(rule.get("vegetarian") for rule in rules):
        warnings.append("Vegetarisch richtet sich nach den gespeicherten Rezept-Tags; bitte Zutaten bei Bedarf prüfen.")
    missing_quotas = []
    for quota in quotas:
        category = quota["category"]
        fulfilled = sum(category in item["categories"] for item in selected)
        missing = max(0, quota["count"] - fulfilled)
        missing_quotas.append({"category": category, "requested": quota["count"], "fulfilled": fulfilled, "missing": missing})
        if missing:
            qualifier = "mindestens" if quota_mode == "minimum" else "exakt"
            warnings.append(f"{CATEGORY_NAMES[category]}: {fulfilled} von {qualifier} {quota['count']} passenden Gerichten gefunden.")
    if quotas or any(rule.get("category") for rule in rules):
        warnings.append("Zutatenarten werden aus gespeicherten Zutaten erkannt; Gerichtstypen aus Rezeptart, Kategorie, Tags und Gerichtsname. Ein Gericht mit mehreren Kategorien zählt in jeder davon; Mengen spielen dabei keine Rolle.")
    if excluded_keys:
        warnings.append("Zutatenausschlüsse vergleichen gespeicherte Zutatennamen und bekannte Synonyme; sie ersetzen keine Allergenprüfung.")
    if avoid_recent_days:
        warnings.append(f"Bereits geplante Gerichte aus den {avoid_recent_days} Tagen vor {anchor.isoformat()} wurden ausgelassen.")
    if not search_complete:
        warnings.append("Das Suchlimit wurde erreicht. Weitere passende Kombinationen können vorhanden sein; die Vorgaben wurden nicht gelockert.")
    assigned_weekdays = {item["weekday"] for item in selected} if rules else set()
    missing_weekdays = [day for day in planned_weekdays(count, rules) if day not in assigned_weekdays] if rules else []
    for day in missing_weekdays:
        warnings.append(f"{WEEKDAY_NAMES[day]}: Kein passendes Gericht zugewiesen. Die Tagesregel und Wochenvorgaben wurden beibehalten.")
    if any(rule.get("min_minutes") is not None or rule.get("max_minutes") is not None for rule in rules):
        warnings.append("Für Tagesregeln mit Zeitvorgaben werden nur Rezepte mit eindeutiger Gesamtzeit zugewiesen.")
    return {"items": [{key: value for key, value in item.items() if not key.startswith("_")} for item in selected],
            "warnings": warnings, "missing_quotas": missing_quotas,
            "missing_weekdays": missing_weekdays,
            "constraints_satisfied": len(selected) == count and vegetarian_found >= vegetarian_count
                                     and all(item["missing"] == 0 for item in missing_quotas) and not missing_weekdays,
            "search_complete": search_complete}


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
