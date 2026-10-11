"""Seeded variety remains reproducible and respects strict eligibility."""
from collections import Counter
from datetime import date
from uuid import UUID

import pytest

from app.recipes.discovery import meal_plan_suggestions
from tests.test_discovery import _recipe
from tests.test_meal_preferences import _personal


@pytest.mark.parametrize("mode", ["free", "quota", "weekday"])
def test_seeds_change_selection_without_weakening_quotas_or_rules(test_db, mode):
    valid = {_recipe(test_db, f"Pasta{i}", tags=("vegan",)) for i in range(8)}
    excluded = _recipe(test_db, "Excluded", tags=("vegan",))
    old = _recipe(test_db, "PreviousWeek", tags=("vegan",))
    _recipe(test_db, "TooSlow", tags=("vegan",), description="Gesamtzeit: 90 Minuten")
    _recipe(test_db, "Meat", ingredients=("Huhn",))
    _recipe(test_db, "Forbidden", ingredients=("Sellerie", "Nudeln"), tags=("vegan",))
    test_db.meal_plan_add(planned_for="2026-10-11", recipe_id=old, planned_servings=2)
    kwargs = dict(count=2, vegetarian_count=2, max_minutes=30, exclude_recipe_ids=[excluded],
                  excluded_ingredients=["Sellerie"], avoid_recent_days=14, week_start=date(2026, 10, 12), quota_mode="minimum")
    if mode != "free": kwargs["category_quotas"] = [{"category": "pasta", "count": 2}]
    if mode == "weekday": kwargs["weekday_rules"] = [{"weekday": 1, "category": "pasta", "max_minutes": 30}, {"weekday": 5, "vegetarian": True}]
    selections = set()
    for seed in range(1, 17):
        result = meal_plan_suggestions(test_db, **kwargs, random_seed=UUID(int=seed))
        assert result == meal_plan_suggestions(test_db, **kwargs, random_seed=UUID(int=seed))
        ids = frozenset(item["recipe_id"] for item in result["items"])
        assert len(ids) == 2 and ids <= valid and result["constraints_satisfied"]
        if mode == "weekday": assert {item["weekday"] for item in result["items"]} == {1, 5}
        selections.add(ids)
    assert len(selections) >= 5


def test_preferences_raise_likelihood_but_allow_other_feasible_recipes(test_db):
    preferred = _recipe(test_db, "Preferred")
    others = {_recipe(test_db, f"Neutral{i}") for i in range(3)}
    _personal(test_db, preferred, favorite=True, rating=5)
    results = Counter(meal_plan_suggestions(test_db, count=1, random_seed=UUID(int=seed))["items"][0]["recipe_id"]
                      for seed in range(1, 101))
    assert results[preferred] > 50
    assert any(results[rid] > 0 for rid in others)


def test_random_seed_wire_validation_and_retry(client, test_db):
    for i in range(5): _recipe(test_db, f"Wire{i}")
    payload = {"count": 2, "random_seed": str(UUID(int=42))}
    first = client.post("/api/discovery/meal-plan", json=payload)
    assert first.status_code == 200
    assert client.post("/api/discovery/meal-plan", json=payload).json() == first.json()
    assert client.post("/api/discovery/meal-plan", json={**payload, "random_seed": "bad"}).status_code == 422
