"""Bounded deterministic selection for exact ingredient-family quotas."""
from __future__ import annotations

import re
from collections import defaultdict


CATEGORY_NAMES = {"pasta": "Nudelgerichte", "potato": "Kartoffelgerichte",
                  "rice": "Reisgerichte", "legume": "Hülsenfruchtgerichte"}
_FAMILIES = {
    "pasta": {"nudeln", "nudel", "pasta", "spaghetti", "penne", "fusilli", "farfalle",
              "tagliatelle", "rigatoni", "maccheroni", "makkaroni", "lasagneplatten",
              "lasagneplatte", "linguine", "fettuccine", "tortellini", "ravioli", "orzo",
              "vollkornnudeln", "bandnudeln", "reisnudeln", "glasnudeln"},
    "potato": {"kartoffel", "kartoffeln", "erdapfel", "erdäpfel"},
    "rice": {"reis", "basmatireis", "jasminreis", "risottoreis", "vollkornreis", "wildreis",
             "naturreis", "langkornreis", "rundkornreis", "sushireis"},
    "legume": {"bohnen", "bohne", "linsen", "linse", "kichererbse", "kichererbsen",
               "erbsen", "erbse", "kidneybohne", "kidneybohnen", "sojabohnen", "edamame"},
}


def ingredient_categories(keys: set[str]) -> list[str]:
    # Whole canonical ingredients / words, never substring matches like Reisessig.
    tokens = set().union(*(set(re.findall(r"[^\W\d_]+", key.casefold())) for key in keys)) if keys else set()
    return [name for name, ingredients in _FAMILIES.items() if tokens & ingredients]


def quota_selection(candidates: list[dict], count: int, vegetarian_count: int,
                    quotas: list[dict], *, transition_limit: int = 2_000_000) -> tuple[list[dict], bool]:
    """Exact DP by constraint signature; keeping count recipes/group is lossless.

    A recipe containing several requested families counts toward all of them.
    Upper bounds and ingredient exclusions are never relaxed. Incomplete output
    reserves at least the number of slots still required by the largest deficit.
    """
    categories = [item["category"] for item in quotas]
    targets = tuple(item["count"] for item in quotas)
    groups = defaultdict(list)
    for item in candidates:
        membership = tuple(int(category in item["categories"]) for category in categories)
        if any(included and target == 0 for included, target in zip(membership, targets)):
            continue
        groups[(membership, bool(item["vegetarian"]))].append(item)
    for items in groups.values():
        items.sort(key=lambda item: (-item["_coverage"], item["name"].casefold(), item["recipe_id"]))
        del items[count:]
    # Important/rare signatures first produce useful partial results even at the hard budget.
    ordered = sorted(groups.items(), key=lambda group: (
        -sum(group[0][0]), -int(group[0][1]), len(group[1]), group[0]))
    zero = (0, 0, *(0 for _ in targets))
    states = {zero: ()}
    transitions = 0
    complete = True
    for (membership, vegetarian), items in ordered:
        next_states = dict(states)
        for state, chosen in states.items():
            n, veg, *filled = state
            for quantity in range(1, min(count - n, len(items)) + 1):
                transitions += 1
                if transitions > transition_limit:
                    complete = False
                    break
                next_n, next_veg = n + quantity, veg + quantity * int(vegetarian)
                if next_n - next_veg > count - vegetarian_count:
                    break
                next_filled = tuple(current + quantity * include for current, include in zip(filled, membership))
                if any(value > target for value, target in zip(next_filled, targets)):
                    break
                key = (next_n, next_veg, *next_filled)
                selection = chosen + tuple(items[:quantity])
                existing = next_states.get(key)
                if existing is None or _quality(selection) > _quality(existing):
                    next_states[key] = selection
            if not complete:
                break
        states = next_states
        if not complete:
            break
    eligible = []
    for state, chosen in states.items():
        n, veg, *filled = state
        deficits = [max(0, target - value) for target, value in zip(targets, filled)]
        vegetarian_missing = max(0, vegetarian_count - veg)
        if n + max([vegetarian_missing, *deficits], default=0) > count:
            continue
        eligible.append(((-sum(deficits) - vegetarian_missing, n, _quality(chosen)), chosen))
    best = max(eligible, key=lambda entry: entry[0])[1] if eligible else ()
    return list(best), complete


def _quality(items):
    return (sum(item["_coverage"] for item in items),
            len({item["_category"] for item in items}),
            tuple(-item["recipe_id"] for item in items))
