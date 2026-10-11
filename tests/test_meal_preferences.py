"""Household preferences rank feasible weeks without weakening hard rules."""
import pytest

from tests.test_discovery import _recipe, discovery_accounts as discovery_accounts


def _suggest(client, *, mode="free", **payload):
    criteria = {"count": 1, "week_start": "2026-10-12", **payload}
    if mode in {"quota", "weekday"}:
        criteria.setdefault("category_quotas", [{"category": "pasta", "count": criteria["count"]}])
    if mode == "weekday":
        criteria.setdefault("weekday_rules", [{"weekday": 5, "category": "pasta", "max_minutes": 30}])
    response = client.post("/api/discovery/meal-plan", json=criteria)
    assert response.status_code == 200, response.text
    return response.json()


def _personal(db, recipe_id, *, favorite=False, rating=0):
    # The auth-bypassed admin fixture uses legacy account zero. Real household
    # tests below deliberately set preferences through authenticated endpoints.
    with db.conn() as connection:
        connection.execute("UPDATE recipes SET is_favorite=?,rating=? WHERE id=?",
                           (int(favorite), rating, recipe_id))


@pytest.mark.parametrize("mode", ["free", "quota", "weekday"])
def test_preference_precedes_coverage_and_group_truncation(client, test_db, mode):
    neutrals = [_recipe(test_db, f"A Neutral {index}", ("Nudeln", "Tomaten")) for index in range(5)]
    preferred = _recipe(test_db, "Z Favorite", ("Nudeln", "Karotten"))
    _personal(test_db, preferred, favorite=True, rating=4)
    result = _suggest(client, mode=mode, count=2, available_ingredients=["Tomaten"])
    assert len(result["items"]) == 2 and result["constraints_satisfied"]
    assert preferred in {item["recipe_id"] for item in result["items"]}
    assert len({item["recipe_id"] for item in result["items"]} & set(neutrals)) == 1
    assert _suggest(client, mode=mode, count=2, available_ingredients=["Tomaten"]) == result
    item = next(item for item in result["items"] if item["recipe_id"] == preferred)
    assert item["preference_reasons"] == ["Favorit im Haushalt", "4 von 5 Sternen im Haushalt"]
    assert not any(key.startswith("_") for key in item)
    assert "rating" not in item and "account_id" not in item
    assert any("Rückblick bleiben verbindlich" in warning for warning in result["warnings"])


@pytest.mark.parametrize("mode", ["quota", "weekday"])
@pytest.mark.parametrize("preferred_vegetarian", [False, True])
def test_dp_compares_preferences_across_distinct_signatures(client, test_db, mode, preferred_vegetarian):
    # Different vegetarian flags prevent both recipes sharing a truncated group;
    # the DP state comparison must also put preference before ingredient coverage.
    _recipe(test_db, "A Neutral", ("Pasta", "Tomaten"),
            tags=() if preferred_vegetarian else ("vegan",))
    preferred = _recipe(test_db, "Z Preferred", ("Pasta", "Karotten"),
                        tags=("vegan",) if preferred_vegetarian else ())
    _personal(test_db, preferred, rating=5)
    result = _suggest(client, mode=mode, available_ingredients=["Tomaten"])
    assert [item["recipe_id"] for item in result["items"]] == [preferred]
    assert result["constraints_satisfied"]


def test_ratings_and_favorites_have_ordered_soft_weights(client, test_db):
    expected = []
    # Reverse alphabetical order makes the preference comparison observable.
    for name, favorite, rating in [
        ("Z LovedFavorite", True, 5), ("Y Loved", False, 5),
        ("X Liked", False, 4), ("C Unrated", False, 0),
        ("D NeutralThree", False, 3), ("B PoorFavorite", True, 2),
        ("A BadFavorite", True, 1),
    ]:
        recipe = _recipe(test_db, name)
        _personal(test_db, recipe, favorite=favorite, rating=rating)
        expected.append(recipe)
    result = _suggest(client, count=7)
    assert [item["recipe_id"] for item in result["items"]] == expected
    assert result["items"][3]["preference_reasons"] == []
    assert result["items"][4]["preference_reasons"] == ["3 von 5 Sternen im Haushalt"]


@pytest.mark.parametrize("mode", ["free", "quota", "weekday"])
@pytest.mark.parametrize("rating", [1, 2])
def test_low_rated_favorite_is_below_unrated_but_does_not_leave_empty_slots(client, test_db, mode, rating):
    low = _recipe(test_db, "A Low Rated Favorite")
    _personal(test_db, low, favorite=True, rating=rating)
    unrated = _recipe(test_db, "Z Unrated")
    one = _suggest(client, mode=mode)
    assert [item["recipe_id"] for item in one["items"]] == [unrated]
    full = _suggest(client, mode=mode, count=2)
    assert {item["recipe_id"] for item in full["items"]} == {low, unrated}
    assert full["constraints_satisfied"] and full["search_complete"]
    only_low = _suggest(client, mode=mode, exclude_recipe_ids=[unrated])
    assert [item["recipe_id"] for item in only_low["items"]] == [low]
    assert only_low["constraints_satisfied"]


@pytest.mark.parametrize("mode", ["free", "quota", "weekday"])
def test_preferences_never_override_hard_constraints_or_previous_weeks(client, test_db, mode):
    wrong_time = _recipe(test_db, "A Too Slow", description="Gesamtzeit: 90 Minuten", tags=("vegan",))
    unknown_time = _recipe(test_db, "B Unknown Time", description="Schnell kochen", tags=("vegan",))
    wrong_tag = _recipe(test_db, "C Not Vegetarian")
    wrong_ingredient = _recipe(test_db, "D Mushrooms", ("Nudeln", "Champignons"), tags=("vegan",))
    excluded = _recipe(test_db, "E Explicit Exclusion", tags=("vegan",))
    recent = _recipe(test_db, "F Previous Week", tags=("vegan",))
    for recipe in (wrong_time, unknown_time, wrong_tag, wrong_ingredient, excluded, recent):
        _personal(test_db, recipe, favorite=True, rating=5)
    test_db.meal_plan_add(planned_for="2026-10-05", recipe_id=recent, planned_servings=2)
    allowed = _recipe(test_db, "Z Allowed", tags=("vegan",))
    criteria = dict(mode=mode, vegetarian_count=1, max_minutes=30, excluded_ingredients=["Pilze"],
                    exclude_recipe_ids=[excluded], avoid_recent_days=14)
    result = _suggest(client, **criteria)
    assert [item["recipe_id"] for item in result["items"]] == [allowed]
    assert result["constraints_satisfied"]
    missing = _suggest(client, **{**criteria, "exclude_recipe_ids": [excluded, allowed]})
    assert missing["items"] == [] and not missing["constraints_satisfied"]


def test_preferred_recipe_cannot_override_a_day_rule_or_zero_quota(client, test_db):
    preferred = _recipe(test_db, "Favorite Pasta", ("Nudeln",), tags=("vegan",))
    _personal(test_db, preferred, favorite=True, rating=5)
    rules = [{"weekday": 5, "category": "pasta", "min_minutes": 45}]
    assert _suggest(client, mode="weekday", weekday_rules=rules)["items"] == []
    assert _suggest(client, mode="quota", category_quotas=[{"category": "pasta", "count": 0}])["items"] == []


@pytest.mark.parametrize("mode", ["free", "quota", "weekday"])
def test_neutral_ratings_preserve_prior_deterministic_selection(client, test_db, mode):
    first = _recipe(test_db, "A Neutral")
    second = _recipe(test_db, "B Neutral")
    _recipe(test_db, "Z Neutral")
    before = _suggest(client, mode=mode, count=2)
    _personal(test_db, first, rating=3)
    _personal(test_db, second, rating=3)
    after = _suggest(client, mode=mode, count=2)
    assert [item["recipe_id"] for item in before["items"]] == [first, second]
    assert [item["recipe_id"] for item in after["items"]] == [first, second]


@pytest.mark.parametrize("mode", ["free", "quota", "weekday"])
def test_household_preferences_are_isolated_and_global_values_do_not_leak(
    client, test_db, discovery_accounts, mode,
):
    _, login = discovery_accounts
    neutral = _recipe(test_db, "A Neutral")
    global_favorite = _recipe(test_db, "B Global Favorite")
    anna_favorite = _recipe(test_db, "Z Anna Favorite")
    _personal(test_db, global_favorite, favorite=True, rating=5)
    login("anna-discovery")
    assert client.post(f"/api/recipes/{anna_favorite}/favorite").status_code == 200
    assert client.post(f"/api/recipes/{anna_favorite}/rating?value=4").status_code == 200
    anna = _suggest(client, mode=mode)
    assert anna["items"][0]["recipe_id"] == anna_favorite
    assert anna["items"][0]["preference_reasons"] == ["Favorit im Haushalt", "4 von 5 Sternen im Haushalt"]

    login("bert-discovery")
    bert = _suggest(client, mode=mode, count=3)
    assert [item["recipe_id"] for item in bert["items"]] == [neutral, global_favorite, anna_favorite]
    assert all(item["preference_reasons"] == [] for item in bert["items"])
    assert not any("Favoriten" in warning for warning in bert["warnings"])
    assert client.post(f"/api/recipes/{anna_favorite}/rating?value=1").status_code == 200
    assert _suggest(client, mode=mode)["items"][0]["recipe_id"] == neutral
    login("anna-discovery")
    assert _suggest(client, mode=mode) == anna


def test_shopping_exclusions_are_not_taste_dislikes(client, test_db):
    _recipe(test_db, "A Neutral Rice", ("Reis",))
    favorite = _recipe(test_db, "Z Favorite Pasta", ("Nudeln",))
    _personal(test_db, favorite, favorite=True)
    test_db.shopping_exclusion_set("nudeln", True)
    result = _suggest(client)
    assert [item["recipe_id"] for item in result["items"]] == [favorite]
