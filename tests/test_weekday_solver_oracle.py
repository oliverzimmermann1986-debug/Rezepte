"""Compare coupled day/ingredient planning with independent exhaustive search."""
from itertools import permutations
from random import Random

from app.recipes.meal_constraints import weekday_selection


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


def test_weekday_solver_matches_exhaustive_feasibility_and_never_relaxes_hard_rules():
    random = Random(20261011)
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
                    and all(sum(quota["category"] in recipe["categories"] for recipe in selection)
                            == quota["count"] for quota in quotas))

        possible = any(exact(selection)
                       and all(_fits(recipe, rules_by_day.get(day, {}))
                               for day, recipe in zip(days, selection))
                       for selection in permutations(candidates, count))
        selected, complete = weekday_selection(candidates, count, vegetarian_count, quotas, rules)
        assert complete, case
        assert len({recipe["recipe_id"] for recipe in selected}) == len(selected), case
        assert len({recipe["weekday"] for recipe in selected}) == len(selected), case
        assert all(recipe["weekday"] in days and _fits(recipe, rules_by_day.get(recipe["weekday"], {}))
                   for recipe in selected), case
        assert all(sum(quota["category"] in recipe["categories"] for recipe in selected)
                   <= quota["count"] for quota in quotas), case
        assert (len(selected) == count and exact(selected)) == possible, case
        assert weekday_selection(list(reversed(candidates)), count, vegetarian_count, quotas,
                                 list(reversed(rules))) == (selected, complete), case
        feasible_cases += possible
        impossible_cases += not possible
    assert feasible_cases > 10 and impossible_cases > 10
