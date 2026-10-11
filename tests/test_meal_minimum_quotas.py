"""Minimums may be exceeded; exclusions, history and day rules stay binding."""
import pytest

from app.recipes.meal_suggestion_pdf import _snapshot_warnings
from tests.test_discovery import _recipe


@pytest.mark.parametrize("weekday_rules", [[], [{"weekday": 1, "category": "salad"}]])
def test_minimum_fills_week_even_when_all_recipes_exceed_target(client, test_db, weekday_rules):
    ids = {_recipe(test_db, f"Nudelsalat {i}", ("Nudeln", "Tomaten"), tags=("vegan",)) for i in range(3)}
    payload = {"count": 3, "quota_mode": "minimum", "week_start": "2026-10-12",
               "category_quotas": [{"category": "pasta", "count": 1}, {"category": "salad", "count": 2}],
               "weekday_rules": weekday_rules}
    response = client.post("/api/discovery/meal-plan", json=payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert {row["recipe_id"] for row in result["items"]} == ids
    assert result["constraints_satisfied"] and result["search_complete"]
    assert all(row["fulfilled"] == 3 and row["missing"] == 0 for row in result["missing_quotas"])
    if weekday_rules:
        assert {row["weekday"] for row in result["items"]} == {1, 2, 3}
    # Older clients still get the exact contract they requested.
    payload.pop("quota_mode")
    assert not client.post("/api/discovery/meal-plan", json=payload).json()["constraints_satisfied"]


@pytest.mark.parametrize("weekday_rules", [[], [{"weekday": 3, "category": "soup"}]])
def test_minimum_preserves_zero_exclusion_and_previous_weeks(client, test_db, weekday_rules):
    _recipe(test_db, "Verbotene Kartoffelsuppe", ("Kartoffeln",))
    previous = _recipe(test_db, "Alte Reissuppe", ("Reis",))
    test_db.meal_plan_add(planned_for="2026-10-05", recipe_id=previous, planned_servings=2)
    allowed = {_recipe(test_db, f"Reissuppe {i}", ("Reis",)) for i in range(3)}
    result = client.post("/api/discovery/meal-plan", json={
        "count": 3, "quota_mode": "minimum", "week_start": "2026-10-12", "avoid_recent_days": 7,
        "category_quotas": [{"category": "soup", "count": 1}, {"category": "potato", "count": 0}],
        "weekday_rules": weekday_rules}).json()
    assert {row["recipe_id"] for row in result["items"]} == allowed
    assert result["constraints_satisfied"]


def test_missing_minimum_is_reported_and_not_filled_with_unrelated_recipe(client, test_db):
    _recipe(test_db, "Ein Nudelsalat", ("Nudeln",))
    for i in range(3):
        _recipe(test_db, f"Reis {i}", ("Reis",))
    result = client.post("/api/discovery/meal-plan", json={"count": 3, "quota_mode": "minimum",
        "category_quotas": [{"category": "pasta", "count": 2}]}).json()
    assert not result["constraints_satisfied"]
    assert len(result["items"]) == 2
    assert result["missing_quotas"][0]["missing"] == 1
    assert any("mindestens 2" in warning for warning in result["warnings"])


def test_pdf_accepts_exceeded_minimum_but_warns_about_zero_exclusion():
    from datetime import date
    criteria = {"count": 2, "vegetarian_count": 0, "quota_mode": "minimum", "max_minutes": None,
                "category_quotas": [{"category": "pasta", "count": 1}, {"category": "salad", "count": 0}],
                "excluded_ingredients": [], "exclude_recipe_ids": [], "weekday_rules": []}
    rows = [{"recipe_id": i, "name": "Nudelsalat", "date": date(2026, 10, 12 + i), "vegetarian": True,
             "categories": ["pasta", "salad"], "_complete": True, "_keys": {"nudeln"}, "total_minutes": 20}
            for i in range(2)]
    warnings = _snapshot_warnings(criteria, rows, set())
    assert not any("Nudelgerichte:" in warning for warning in warnings)
    assert any("Salate:" in warning for warning in warnings)


def test_unknown_quota_mode_is_rejected(client):
    assert client.post("/api/discovery/meal-plan", json={"quota_mode": "approximate"}).status_code == 422
