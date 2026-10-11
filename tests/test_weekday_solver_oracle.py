"""Compare coupled day/ingredient planning with independent exhaustive search."""
from itertools import permutations
from random import Random
import pytest

from app.recipes.meal_constraints import quota_selection, weekday_selection


def _fits(recipe, rule):
    if rule.get("category") not in (None, *recipe["categories"]):
        return False
    if rule.get("vegetarian") and not recipe["vegetarian"]:
        return False
    time = recipe["total_minutes"]
    for key, comparison in (("min_minutes", lambda x, y: x >= y),
                            ("max_minutes", lambda x, y: x <= y)):
        if rule.get(key) is not None and (time is None or not comparison(time, rule[key])):
            return False
    return True


@pytest.mark.parametrize("quota_mode", ["exact", "minimum"])
def test_weekday_solver_matches_exhaustive_feasibility_and_never_relaxes_hard_rules(quota_mode):
    random = Random(20261011)
    preference_random = Random(20261012)
    feasible_cases = 0
    impossible_cases = 0
    for case in range(180):
        count = random.randint(1, 4)
        vegetarian_count = random.randint(0, count)
        candidates = [{
            "recipe_id": index + 1, "name": f"Recipe {index}",
            "vegetarian": bool(random.getrandbits(1)),
            "categories": [name for name in ("pasta", "potato", "rice") if random.getrandbits(1)],
            "total_minutes": random.choice([None, 15, 20, 30, 60]),
            "_coverage": random.choice([0, 0.5, 1]), "_category": str(index % 3),
            "_preference_score": preference_random.choice([-5, -3, -1, 0, 1, 2, 3, 4]),
        } for index in range(random.randint(2, 7))]
        quotas = [{"category": name, "count": random.randint(0, count)}
                  for name in random.sample(["pasta", "potato", "rice"], random.randint(0, 3))]
        days_with_rules = random.sample(range(1, 8), random.randint(1, count))
        rules = [{"weekday": day, "category": random.choice([None, "pasta", "potato"]),
                  "vegetarian": bool(random.getrandbits(1)),
                  "min_minutes": random.choice([None, 15, 20]),
                  "max_minutes": random.choice([None, 30, 60])} for day in days_with_rules]
        days = sorted(days_with_rules + [day for day in range(1, 8)
                                       if day not in days_with_rules][:count - len(days_with_rules)])
        rules_by_day = {rule["weekday"]: rule for rule in rules}

        def exact(selection):
            return (sum(recipe["vegetarian"] for recipe in selection) >= vegetarian_count
                    and all((sum(quota["category"] in recipe["categories"] for recipe in selection)
                             >= quota["count"] if quota_mode == "minimum" and quota["count"] > 0
                             else sum(quota["category"] in recipe["categories"] for recipe in selection)
                             == quota["count"]) for quota in quotas))

        quota_scores = []
        weekday_scores = []
        for selection in permutations(candidates, count):
            if exact(selection):
                score = sum(recipe["_preference_score"] for recipe in selection)
                quota_scores.append(score)
                if all(_fits(recipe, rules_by_day.get(day, {})) for day, recipe in zip(days, selection)):
                    weekday_scores.append(score)
        possible = bool(weekday_scores)
        selected, complete = weekday_selection(candidates, count, vegetarian_count, quotas, rules, quota_mode=quota_mode)
        assert complete, case
        assert len({recipe["recipe_id"] for recipe in selected}) == len(selected), case
        assert len({recipe["weekday"] for recipe in selected}) == len(selected), case
        assert all(recipe["weekday"] in days and _fits(recipe, rules_by_day.get(recipe["weekday"], {}))
                   for recipe in selected), case
        assert all(sum(quota["category"] in recipe["categories"] for recipe in selected)
                   <= quota["count"] for quota in quotas if quota_mode == "exact" or quota["count"] == 0), case
        assert (len(selected) == count and exact(selected)) == possible, case
        if possible:
            assert sum(recipe["_preference_score"] for recipe in selected) == max(weekday_scores), case
        assert weekday_selection(list(reversed(candidates)), count, vegetarian_count, quotas,
                                 list(reversed(rules)), quota_mode=quota_mode) == (selected, complete), case
        quota_selected, quota_complete = quota_selection(candidates, count, vegetarian_count, quotas, quota_mode=quota_mode)
        assert quota_complete, case
        assert (len(quota_selected) == count and exact(quota_selected)) == bool(quota_scores), case
        if quota_scores:
            assert sum(recipe["_preference_score"] for recipe in quota_selected) == max(quota_scores), case
        feasible_cases += possible
        impossible_cases += not possible
    assert feasible_cases > 10 and impossible_cases > 10
