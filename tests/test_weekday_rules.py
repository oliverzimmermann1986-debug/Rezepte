"""Joint day assignments and weekly quotas, with strict time and input bounds."""
import pytest

from app.recipes.meal_constraints import planned_weekdays, weekday_selection
from tests.test_discovery import _recipe, discovery_accounts as discovery_accounts


def _suggest(client, **payload):
    response = client.post("/api/discovery/meal-plan", json={"week_start": "2026-10-12", **payload})
    assert response.status_code == 200, response.text
    return response.json()


def test_weekday_slots_keep_configured_days_and_fill_earliest_gaps():
    assert planned_weekdays(4, [{"weekday": 5}, {"weekday": 7}]) == [1, 2, 5, 7]
    assert planned_weekdays(1, [{"weekday": 5}]) == [5]


def test_quota_and_day_assignment_are_solved_together(client, test_db):
    # A quota-only solution would take the alphabetically first slow pasta,
    # leaving no recipe for Monday. The joint solution must reserve fast pasta.
    _recipe(test_db, "ASlowPasta", ("Nudeln",), description="Gesamtzeit: 90 Minuten")
    fast = _recipe(test_db, "ZFastPasta", ("Penne",), description="Gesamtzeit: 15 Minuten")
    potato = _recipe(test_db, "Potatoes", ("Kartoffeln",), description="Gesamtzeit: 45 Minuten")
    result = _suggest(client, count=2, category_quotas=[{"category": "pasta", "count": 1},
        {"category": "potato", "count": 1}], weekday_rules=[{"weekday": 1, "max_minutes": 20},
        {"weekday": 5, "category": "potato"}])
    assert [(item["weekday"], item["recipe_id"]) for item in result["items"]] == [(1, fast), (5, potato)]
    assert result["constraints_satisfied"] and result["search_complete"]
    assert result["missing_weekdays"] == []


def test_coupled_solver_avoids_greedy_day_overlap_trap(client, test_db):
    # Versatile pasta fits both days; rice fits only Monday. Assigning pasta to
    # the first day greedily would strand Friday even though a full plan exists.
    pasta = _recipe(test_db, "AAnyDay", ("Nudeln",), description="Gesamtzeit: 15 Minuten")
    rice = _recipe(test_db, "ZMondayOnly", ("Reis",), description="Gesamtzeit: 15 Minuten")
    result = _suggest(client, count=2, weekday_rules=[{"weekday": 1, "max_minutes": 20},
        {"weekday": 5, "category": "pasta"}], category_quotas=[{"category": "pasta", "count": 1}])
    assert [(item["weekday"], item["recipe_id"]) for item in result["items"]] == [(1, rice), (5, pasta)]
    assert result["constraints_satisfied"]


def test_missing_day_is_not_filled_with_a_rule_breaking_recipe(client, test_db):
    potato = _recipe(test_db, "PotatoOnly", ("Kartoffeln",))
    other_potato = _recipe(test_db, "OtherPotato", ("Kartoffeln",))
    result = _suggest(client, count=2, weekday_rules=[{"weekday": 5, "category": "pasta"}])
    assert len(result["items"]) == 1
    assert result["items"][0]["weekday"] == 1
    assert result["items"][0]["recipe_id"] in {potato, other_potato}
    assert result["missing_weekdays"] == [5]
    assert not result["constraints_satisfied"] and result["search_complete"]
    assert any("Freitag" in warning for warning in result["warnings"])


def test_unknown_time_only_allowed_in_unbounded_day(client, test_db):
    unknown = _recipe(test_db, "Unknown", ("Pasta",), description="Keine Gesamtzeit")
    known = _recipe(test_db, "Known", ("Kartoffeln",), description="Gesamtzeit: 15 Minuten")
    result = _suggest(client, count=2, weekday_rules=[{"weekday": 1, "max_minutes": 20}])
    assert [(item["weekday"], item["recipe_id"]) for item in result["items"]] == [(1, known), (2, unknown)]
    bounded = _suggest(client, count=1, weekday_rules=[{"weekday": 7, "category": "pasta", "min_minutes": 1}])
    assert bounded["items"] == [] and bounded["missing_weekdays"] == [7]


def test_minimum_maximum_and_vegetarian_rule_all_apply_to_assigned_day(client, test_db):
    _recipe(test_db, "TooQuick", ("Nudeln",), description="Gesamtzeit: 20 Minuten", tags=("vegan",))
    _recipe(test_db, "TooSlow", ("Nudeln",), description="Gesamtzeit: 120 Minuten", tags=("vegan",))
    _recipe(test_db, "NotVegetarian", ("Nudeln",), description="Gesamtzeit: 60 Minuten")
    right = _recipe(test_db, "Sunday", ("Nudeln",), description="Gesamtzeit: 60 Minuten", tags=("vegetarisch",))
    result = _suggest(client, count=1, weekday_rules=[{"weekday": 7, "category": "pasta",
        "min_minutes": 45, "max_minutes": 90, "vegetarian": True}])
    assert [(item["weekday"], item["recipe_id"]) for item in result["items"]] == [(7, right)]
    assert result["constraints_satisfied"]


def test_global_time_and_exclusions_are_not_overridden_by_day_rule(client, test_db):
    _recipe(test_db, "Excluded", ("Champignons", "Nudeln"), description="Gesamtzeit: 15 Minuten")
    _recipe(test_db, "Long", ("Nudeln",), description="Gesamtzeit: 60 Minuten")
    right = _recipe(test_db, "Short", ("Nudeln",), description="Gesamtzeit: 20 Minuten")
    result = _suggest(client, count=1, max_minutes=30, excluded_ingredients=["Pilze"],
        weekday_rules=[{"weekday": 5, "category": "pasta", "max_minutes": 90}])
    assert result["items"][0]["recipe_id"] == right
    assert result["constraints_satisfied"]


def test_zero_global_quota_is_not_overridden_by_day_rule(client, test_db):
    _recipe(test_db, "Pasta", ("Nudeln",))
    result = _suggest(client, count=1, category_quotas=[{"category": "pasta", "count": 0}],
        weekday_rules=[{"weekday": 5, "category": "pasta"}])
    assert result["items"] == [] and result["missing_weekdays"] == [5]
    assert not result["constraints_satisfied"]


def test_one_dish_replacement_keeps_late_weekday(client, test_db):
    old = _recipe(test_db, "OldFriday", ("Nudeln",))
    replacement = _recipe(test_db, "NewFriday", ("Nudeln",))
    result = _suggest(client, count=1, exclude_recipe_ids=[old],
        category_quotas=[{"category": "pasta", "count": 1}],
        weekday_rules=[{"weekday": 5, "category": "pasta"}])
    assert [(item["weekday"], item["recipe_id"]) for item in result["items"]] == [(5, replacement)]


def test_without_rules_keeps_unassigned_legacy_results(client, test_db):
    _recipe(test_db, "Legacy", ("Reis",))
    without = _suggest(client, count=1)
    explicit_empty = _suggest(client, count=1, weekday_rules=[])
    assert without == explicit_empty
    assert "weekday" not in without["items"][0]
    assert without["missing_weekdays"] == [] and without["constraints_satisfied"]


def test_weekday_suggestions_remain_household_scoped(client, test_db, discovery_accounts):
    users, login = discovery_accounts
    own = _recipe(test_db, "Own", ("Nudeln",), owner=users["anna-discovery"])
    _recipe(test_db, "Other", ("Nudeln",), owner=users["bert-discovery"])
    login("anna-discovery")
    result = _suggest(client, count=2, weekday_rules=[{"weekday": 5, "category": "pasta"}])
    assert {item["recipe_id"] for item in result["items"]} == {own}
    assert result["items"][0]["weekday"] == 5
    assert result["missing_weekdays"] == [1]


@pytest.mark.parametrize("payload", [
    {"weekday_rules": [{"weekday": 0}]},
    {"weekday_rules": [{"weekday": 8}]},
    {"weekday_rules": [{"weekday": 1, "category": "unknown"}]},
    {"weekday_rules": [{"weekday": 1, "min_minutes": 0}]},
    {"weekday_rules": [{"weekday": 1, "max_minutes": 1441}]},
    {"weekday_rules": [{"weekday": 1, "min_minutes": 45, "max_minutes": 30}]},
    {"weekday_rules": [{"weekday": 1}, {"weekday": 1}]},
    {"count": 1, "weekday_rules": [{"weekday": 1}, {"weekday": 2}]},
    {"week_start": None, "weekday_rules": [{"weekday": 1}]},
    {"week_start": "2026-10-13", "weekday_rules": [{"weekday": 1}]},
    {"week_start": "9999-12-27", "weekday_rules": [{"weekday": 1}]},
])
def test_invalid_weekday_rules_are_rejected(client, payload):
    response = client.post("/api/discovery/meal-plan", json={"week_start": "2026-10-12", **payload})
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("limits", [{"transition_limit": 1}, {"state_limit": 2}])
def test_solver_budget_is_honest_and_never_relaxes_day_rules(limits):
    candidates = [{"recipe_id": index, "name": f"Pasta{index}", "vegetarian": False,
                   "categories": ["pasta"], "total_minutes": 15, "_coverage": 0, "_category": "test"}
                  for index in range(8)]
    rules = [{"weekday": 1, "category": "pasta"}, {"weekday": 7, "category": "potato"}]
    selected, complete = weekday_selection(candidates, 3, 0, [], rules, **limits)
    assert not complete
    assert all(item["weekday"] in {1, 2} for item in selected)
    assert len({item["recipe_id"] for item in selected}) == len(selected)


def test_day_selection_is_deterministic_across_input_order():
    candidates = [{"recipe_id": index, "name": f"Recipe{index}", "vegetarian": index % 2 == 0,
                   "categories": ["pasta"] if index % 3 else ["potato"], "total_minutes": index * 10,
                   "_coverage": .2, "_category": "test"} for index in range(1, 9)]
    rules = [{"weekday": 1, "max_minutes": 25}, {"weekday": 5, "category": "pasta"}]
    first = weekday_selection(candidates, 4, 1, [{"category": "potato", "count": 1}], rules)
    second = weekday_selection(list(reversed(candidates)), 4, 1, [{"category": "potato", "count": 1}], list(reversed(rules)))
    assert first == second
