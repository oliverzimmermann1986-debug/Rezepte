"""Bounded deterministic selection for exact ingredient and dish-type quotas."""
from __future__ import annotations

import re
from collections import defaultdict


CATEGORY_NAMES = {"pasta": "Nudelgerichte", "potato": "Kartoffelgerichte",
                  "rice": "Reisgerichte", "legume": "Hülsenfruchtgerichte",
                  "salad": "Salate", "soup": "Suppen", "stew": "Eintöpfe",
                  "casserole": "Aufläufe", "pizza": "Pizza", "wrap": "Wraps"}
WEEKDAY_NAMES = {1: "Montag", 2: "Dienstag", 3: "Mittwoch", 4: "Donnerstag",
                 5: "Freitag", 6: "Samstag", 7: "Sonntag"}
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
_EXCLUSION_FAMILIES = {
    "pilz": {"pilz", "pilze", "champignon", "champignons", "pfifferling", "pfifferlinge",
             "steinpilz", "steinpilze", "shiitake", "shii", "austernpilz", "austernpilze",
             "kräuterseitling", "kräuterseitlinge", "portobello", "morchel", "morcheln"},
    "pilze": {"pilz", "pilze", "champignon", "champignons", "pfifferling", "pfifferlinge",
              "steinpilz", "steinpilze", "shiitake", "austernpilz", "austernpilze",
              "kräuterseitling", "kräuterseitlinge", "portobello", "morchel", "morcheln"},
}


def excluded_ingredient_match(keys: set[str], excluded: set[str]) -> bool:
    if keys & excluded:
        return True
    tokens = set().union(*(set(re.findall(r"[^\W\d_]+", key.casefold())) for key in keys)) if keys else set()
    for name in excluded:
        if name in _EXCLUSION_FAMILIES and tokens & _EXCLUSION_FAMILIES[name]:
            return True
        # A qualified ingredient such as Milch (laktosefrei) still contains Milch.
        # Whole words avoid matching Reisessig, Kokosmilch or Kartoffelstärke.
        words = set(re.findall(r"[^\W\d_]+", name.casefold()))
        if words and any(words <= set(re.findall(r"[^\W\d_]+", key.casefold())) for key in keys):
            return True
    return False


def ingredient_categories(keys: set[str]) -> list[str]:
    # Whole canonical ingredients / words, never substring matches like Reisessig.
    tokens = set().union(*(set(re.findall(r"[^\W\d_]+", key.casefold())) for key in keys)) if keys else set()
    return [name for name, ingredients in _FAMILIES.items() if tokens & ingredients]


_DISH_MARKERS = {
    "salad": {"salat", "salate", "salad", "salads"},
    "soup": {"suppe", "suppen", "soup", "soups"},
    "stew": {"eintopf", "eintöpfe", "stew", "stews"},
    "casserole": {"auflauf", "aufläufe", "gratin", "gratins", "lasagne", "lasagna", "casserole"},
    "pizza": {"pizza", "pizzen", "pizzas", "flammkuchen"},
    "wrap": {"wrap", "wraps", "burrito", "burritos"},
}


def recipe_categories(recipe: dict, tags: set[str], keys: set[str]) -> list[str]:
    """Dish types use recipe metadata, never an ingredient like salad or stock.

    Name fallback uses the main dish before 'mit/dazu/auf/und', so a wrap
    with a side salad does not count as a salad. German compound dish names
    such as Kartoffelsuppe and Nudelsalat remain recognizable.
    """
    explicit = " ".join([str(recipe.get("type") or ""), str(recipe.get("category") or ""), *tags]).casefold()
    metadata_words = set(re.findall(r"[^\W\d_]+", explicit))
    title = re.split(r"\b(?:mit|dazu|auf|und|zu|with|and)\b", str(recipe.get("name") or "").casefold(), maxsplit=1)[0]
    title_words = set(re.findall(r"[^\W\d_]+", title))
    categories = ingredient_categories(keys)
    for category, markers in _DISH_MARKERS.items():
        if metadata_words & markers or any(word.endswith(marker) for word in title_words for marker in markers):
            categories.append(category)
    return categories


def planned_weekdays(count: int, rules: list[dict]) -> list[int]:
    """Keep every configured day, then fill the earliest other days of that week."""
    days = {rule["weekday"] for rule in rules}
    for weekday in range(1, 8):
        if len(days) >= count:
            break
        days.add(weekday)
    return sorted(days)


def matches_weekday_rule(item: dict, rule: dict) -> bool:
    if rule.get("category") and rule["category"] not in item["categories"]:
        return False
    if rule.get("vegetarian") and not item["vegetarian"]:
        return False
    minimum, maximum, total = rule.get("min_minutes"), rule.get("max_minutes"), item["total_minutes"]
    if (minimum is not None or maximum is not None) and total is None:
        return False
    return (minimum is None or total >= minimum) and (maximum is None or total <= maximum)


def weekday_selection(candidates: list[dict], count: int, vegetarian_count: int,
                      quotas: list[dict], rules: list[dict], *,
                      transition_limit: int = 2_000_000,
                      state_limit: int = 50_000) -> tuple[list[dict], bool]:
    """Joint recipe/day/quota DP; day rules are never assigned after selection.

    Identical signatures share quota contribution, vegetarian status and every
    eligible day. Keeping at most count recipes per signature is therefore
    lossless for feasibility. A state records occupied days and fulfilled global
    constraints, so alternative recipes cannot consume a later rule's only slot.
    """
    days = planned_weekdays(count, rules)
    rules_by_day = {rule["weekday"]: rule for rule in rules}
    categories = [quota["category"] for quota in quotas]
    targets = tuple(quota["count"] for quota in quotas)
    rule_mask = sum(1 << (day - 1) for day in rules_by_day)
    groups = defaultdict(list)
    for item in candidates:
        membership = tuple(int(category in item["categories"]) for category in categories)
        if any(included and target == 0 for included, target in zip(membership, targets)):
            continue
        eligible = sum(1 << (day - 1) for day in days
                       if matches_weekday_rule(item, rules_by_day.get(day, {})))
        if eligible:
            groups[(membership, bool(item["vegetarian"]), eligible)].append(item)
    for items in groups.values():
        items.sort(key=lambda item: (-item.get("_preference_score", 0), -item["_coverage"],
                                     item["name"].casefold(), item["recipe_id"]))
        del items[count:]
    ordered = sorted(groups.items(), key=lambda group: (
        group[0][2].bit_count(), -sum(group[0][0]), -int(group[0][1]), len(group[1]), group[0]))
    states = {(0, 0, *(0 for _ in targets)): ()}
    transitions = 0
    complete = True
    for (membership, vegetarian, eligible), items in ordered:
        next_states = dict(states)
        for state, chosen in states.items():
            occupied, veg, *filled = state
            available = eligible & ~occupied
            subset = available
            while subset:
                transitions += 1
                if transitions > transition_limit:
                    complete = False
                    break
                quantity = subset.bit_count()
                next_n, next_veg = occupied.bit_count() + quantity, veg + quantity * int(vegetarian)
                next_filled = tuple(value + quantity * include for value, include in zip(filled, membership))
                if (quantity <= len(items) and next_n - next_veg <= count - vegetarian_count
                        and all(value <= target for value, target in zip(next_filled, targets))):
                    assigned_days = [day for day in days if subset & (1 << (day - 1))]
                    selection = chosen + tuple({**item, "weekday": day}
                                               for item, day in zip(items, assigned_days))
                    key = (occupied | subset, next_veg, *next_filled)
                    existing = next_states.get(key)
                    if existing is None and len(next_states) >= state_limit:
                        complete = False
                        break
                    if existing is None or _quality(selection) > _quality(existing):
                        next_states[key] = selection
                subset = (subset - 1) & available
            if not complete:
                break
        states = next_states
        if not complete:
            break
    eligible_states = []
    for state, chosen in states.items():
        occupied, veg, *filled = state
        n = occupied.bit_count()
        deficits = [max(0, target - value) for target, value in zip(targets, filled)]
        vegetarian_missing = max(0, vegetarian_count - veg)
        if n + max([vegetarian_missing, *deficits], default=0) > count:
            continue
        missing_rules = (rule_mask & ~occupied).bit_count()
        eligible_states.append(((-sum(deficits) - vegetarian_missing - missing_rules,
                                 n, -missing_rules, _quality(chosen)), chosen))
    best = max(eligible_states, key=lambda entry: entry[0])[1] if eligible_states else ()
    return sorted(best, key=lambda item: item["weekday"]), complete


def quota_selection(candidates: list[dict], count: int, vegetarian_count: int,
                    quotas: list[dict], *, transition_limit: int = 2_000_000,
                    state_limit: int = 50_000) -> tuple[list[dict], bool]:
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
        items.sort(key=lambda item: (-item.get("_preference_score", 0), -item["_coverage"],
                                     item["name"].casefold(), item["recipe_id"]))
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
                if existing is None and len(next_states) >= state_limit:
                    complete = False
                    break
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
    return (sum(item.get("_preference_score", 0) for item in items),
            sum(item["_coverage"] for item in items),
            len({item["_category"] for item in items}),
            tuple(-item["recipe_id"] for item in items))
