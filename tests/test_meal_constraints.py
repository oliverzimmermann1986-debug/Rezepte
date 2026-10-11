"""Exact weekly quotas, overlap feasibility, exclusions and household history."""
import pytest

from app.recipes.meal_constraints import excluded_ingredient_match, ingredient_categories, quota_selection
from tests.test_discovery import _recipe, discovery_accounts as discovery_accounts


def test_two_pasta_four_potato_and_one_unrestricted_dish(client, test_db):
    pasta = {_recipe(test_db, f"Pasta{i}", ("Spaghetti", "Tomaten")) for i in range(4)}
    potato = {_recipe(test_db, f"Kartoffeln{i}", ("Kartoffeln", "Zwiebeln")) for i in range(5)}
    free = _recipe(test_db, "FreiesGericht", ("Reis",))
    payload = {"count": 7, "category_quotas": [{"category": "pasta", "count": 2}, {"category": "potato", "count": 4}]}
    response = client.post("/api/discovery/meal-plan", json=payload)
    assert response.status_code == 200, response.text
    result = response.json()
    ids = {item["recipe_id"] for item in result["items"]}
    assert len(ids & pasta) == 2 and len(ids & potato) == 4 and free in ids
    assert result["constraints_satisfied"] and result["search_complete"]
    assert all(item["missing"] == 0 for item in result["missing_quotas"])
    assert client.post("/api/discovery/meal-plan", json=payload).json() == result


def test_solver_avoids_greedy_overlap_trap_and_keeps_vegetarian_requirement(client, test_db):
    _recipe(test_db, "A_Ueberlappung", ("Nudeln", "Kartoffeln"), tags=("vegan",))
    pasta = _recipe(test_db, "B_Nudeln", ("Penne",))
    potato = _recipe(test_db, "C_Kartoffeln", ("Kartoffel",), tags=("vegan",))
    result = client.post("/api/discovery/meal-plan", json={"count": 2, "vegetarian_count": 1,
        "category_quotas": [{"category": "pasta", "count": 1}, {"category": "potato", "count": 1}]}).json()
    assert {item["recipe_id"] for item in result["items"]} == {pasta, potato}
    assert result["constraints_satisfied"]


def test_a_multi_family_recipe_counts_in_each_requested_category(client, test_db):
    both = _recipe(test_db, "NudelnMitKartoffeln", ("Nudeln", "Kartoffeln"))
    result = client.post("/api/discovery/meal-plan", json={"count": 1,
        "category_quotas": [{"category": "pasta", "count": 1}, {"category": "potato", "count": 1}]}).json()
    assert [item["recipe_id"] for item in result["items"]] == [both]
    assert result["items"][0]["categories"] == ["pasta", "potato"]
    assert result["constraints_satisfied"]


def test_infeasible_quota_reserves_missing_slots_and_reports_exact_deficit(client, test_db):
    pasta = _recipe(test_db, "EinPasta", ("Pasta",))
    for index in range(4):
        _recipe(test_db, f"Reis{index}", ("Reis",))
    result = client.post("/api/discovery/meal-plan", json={"count": 3,
        "category_quotas": [{"category": "pasta", "count": 2}]}).json()
    assert len(result["items"]) == 2
    assert pasta in {item["recipe_id"] for item in result["items"]}
    assert result["missing_quotas"] == [{"category": "pasta", "requested": 2, "fulfilled": 1, "missing": 1}]
    assert not result["constraints_satisfied"] and result["search_complete"]


def test_zero_quota_excludes_category_and_does_not_guess_from_recipe_title(client, test_db):
    _recipe(test_db, "TrotzdemPasta", ("Penne",))
    valid = _recipe(test_db, "NudelnameOhneNudeln", ("Reisessig", "Kartoffelstärke", "Karotten"))
    result = client.post("/api/discovery/meal-plan", json={"count": 1,
        "category_quotas": [{"category": "pasta", "count": 0}, {"category": "potato", "count": 0},
                            {"category": "rice", "count": 0}]}).json()
    assert [item["recipe_id"] for item in result["items"]] == [valid]
    assert result["items"][0]["categories"] == []
    assert result["constraints_satisfied"]


def test_legume_and_rice_families_use_whole_ingredient_words():
    assert ingredient_categories({"rote linsen", "basmatireis"}) == ["rice", "legume"]
    assert ingredient_categories({"kartoffelstärke", "reisessig", "sojasauce", "mandelmilch"}) == []


def test_curated_exclusions_cover_mushrooms_and_qualified_milk_without_substrings():
    assert excluded_ingredient_match({"champignon", "nudeln"}, {"pilz"})
    assert excluded_ingredient_match({"getrocknete shiitake"}, {"pilze"})
    assert excluded_ingredient_match({"milch (laktosefrei)"}, {"milch"})
    assert not excluded_ingredient_match({"kokosmilch", "reisessig", "kartoffelstärke"}, {"milch", "reis", "kartoffel"})


def test_exclusion_groups_are_enforced_in_actual_suggestions(client, test_db):
    _recipe(test_db, "Pilzpasta", ("Champignons", "Nudeln"))
    _recipe(test_db, "Milchnudeln", ("Milch (laktosefrei)", "Nudeln"))
    valid = _recipe(test_db, "ReinePasta", ("Nudeln",))
    result = client.post("/api/discovery/meal-plan", json={"count": 3, "excluded_ingredients": ["Pilze", "Milch"]}).json()
    assert [item["recipe_id"] for item in result["items"]] == [valid]


def test_planner_scan_limit_counts_incomplete_rows_before_python_filtering():
    from app.recipes.discovery import meal_plan_suggestions

    class IncompleteCollection:
        calls = 0

        def recipe_list(self, *, limit, offset, **kwargs):
            self.calls += 1
            return [{"id": index} for index in range(offset, min(offset + limit, 20_000))]

        def recipe_ingredients_for_ids(self, ids):
            return {rid: [{"name": "   "}] for rid in ids}

    collection = IncompleteCollection()
    result = meal_plan_suggestions(collection, count=2)
    assert result["items"] == []
    assert not result["search_complete"]
    assert collection.calls == 26


def test_exclusions_match_known_synonyms_and_time_limit_stays_strict(client, test_db):
    _recipe(test_db, "KarottenPasta", ("Möhren", "Nudeln"))
    _recipe(test_db, "LangsamePasta", ("Nudeln",), description="Gesamtzeit: 90 Minuten")
    valid = _recipe(test_db, "TomatenPasta", ("Nudeln", "Tomaten"))
    result = client.post("/api/discovery/meal-plan", json={"count": 2, "max_minutes": 30,
        "excluded_ingredients": ["Karotten"], "category_quotas": [{"category": "pasta", "count": 2}]}).json()
    assert [item["recipe_id"] for item in result["items"]] == [valid]
    assert result["missing_quotas"][0]["missing"] == 1


def test_plan_history_uses_requested_week_and_only_the_current_household(client, test_db, discovery_accounts):
    users, login = discovery_accounts
    own = _recipe(test_db, "EigeneVorwoche", ("Nudeln",))
    other = _recipe(test_db, "FremdeVorwoche", ("Kartoffeln",))
    old = _recipe(test_db, "Aelter", ("Reis",))
    private = _recipe(test_db, "FremdesPrivat", ("Nudeln",), owner=users["bert-discovery"])
    login("anna-discovery")
    for rid, day in ((own, "2026-10-05"), (old, "2026-10-04")):
        assert client.post("/api/meal-plan/items", json={"recipe_id": rid, "planned_for": day, "planned_servings": 2}).status_code == 200
    login("bert-discovery")
    assert client.post("/api/meal-plan/items", json={"recipe_id": other, "planned_for": "2026-10-06", "planned_servings": 2}).status_code == 200
    login("anna-discovery")
    result = client.post("/api/discovery/meal-plan", json={"count": 7, "week_start": "2026-10-12", "avoid_recent_days": 7}).json()
    assert {item["recipe_id"] for item in result["items"]} == {other, old}


@pytest.mark.parametrize("payload", [
    {"category_quotas": [{"category": "unknown", "count": 1}]},
    {"count": 1, "category_quotas": [{"category": "pasta", "count": 2}]},
    {"category_quotas": [{"category": "pasta", "count": -1}]},
    {"category_quotas": [{"category": "pasta", "count": 1}] * 2},
    {"excluded_ingredients": [" "]}, {"excluded_ingredients": ["!!!"]}, {"excluded_ingredients": ["Tomate"] * 101},
    {"avoid_recent_days": 365}, {"week_start": "yesterday"}, {"week_start": 1234567890},
    {"week_start": "0001-01-01", "avoid_recent_days": 28},
])
def test_invalid_new_constraints_are_rejected(client, payload):
    assert client.post("/api/discovery/meal-plan", json=payload).status_code == 422


def test_search_budget_returns_explicit_incomplete_status_without_violating_upper_bounds():
    candidates = [{"recipe_id": index, "name": f"Pasta{index}", "vegetarian": False,
                   "categories": ["pasta"], "_coverage": 0, "_category": "test"} for index in range(8)]
    selected, complete = quota_selection(candidates, 7, 0, [{"category": "pasta", "count": 2}], transition_limit=1)
    assert not complete
    assert len(selected) <= 2
