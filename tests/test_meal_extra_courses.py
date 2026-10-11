"""Optional courses use recipe evidence and retain all household restrictions."""
from datetime import date

import pytest

from app.recipes.meal_constraints import recipe_course
from app.recipes.meal_suggestion_pdf import _snapshot_warnings
from tests.test_discovery import _recipe


@pytest.mark.parametrize("name,course", [("Porridge mit Beeren", "breakfast"), ("Bruschetta", "starter"),
                                       ("Tiramisu", "dessert"), ("Wrap mit Salat", "main"), ("Suppe", "main")])
def test_course_uses_main_dish_name_not_side_dish(name, course):
    assert recipe_course({"name": name}) == course
    assert recipe_course({"name": "Hähnchen mit Tiramisu"}) == "main"


@pytest.mark.parametrize("course", ["breakfast", "starter", "dessert"])
def test_course_filter_keeps_matching_recipe_and_history_exclusion(client, test_db, course):
    old = _recipe(test_db, f"Altes {course}", ("Tomaten",))
    matching = _recipe(test_db, f"Neues {course}", ("Gurke",))
    other = _recipe(test_db, "Hauptgericht", ("Reis",))
    with test_db.conn() as c:
        c.execute("UPDATE recipes SET category=? WHERE id IN (?,?)", (course, old, matching))
    test_db.meal_plan_add(planned_for="2026-10-05", recipe_id=old, planned_servings=2)
    result = client.post("/api/discovery/meal-plan", json={"count": 1, "meal_course": course,
        "avoid_recent_days": 7, "week_start": "2026-10-12", "quota_mode": "minimum"}).json()
    assert [row["recipe_id"] for row in result["items"]] == [matching]
    assert result["items"][0]["meal_course"] == course
    assert result["constraints_satisfied"]
    week = client.get("/api/meal-plan?week_start=2026-10-05").json()
    assert week["days"][0]["items"][0]["meal_course"] == course
    excluded = client.post("/api/discovery/meal-plan", json={"count": 1, "meal_course": course,
        "exclude_recipe_ids": [old, matching]}).json()
    assert excluded["items"] == [] and not excluded["constraints_satisfied"]
    main = client.post("/api/discovery/meal-plan", json={"count": 1, "meal_course": "main"}).json()
    assert [row["recipe_id"] for row in main["items"]] == [other]


def test_pdf_extra_courses_do_not_satisfy_main_weekly_minimums():
    criteria = {"count": 1, "vegetarian_count": 1, "quota_mode": "minimum", "max_minutes": None,
                "category_quotas": [{"category": "pasta", "count": 1}], "excluded_ingredients": [],
                "exclude_recipe_ids": [], "weekday_rules": [], "additional_courses": [{"weekday": 1, "course": "dessert"}]}
    rows = [{"recipe_id": 1, "name": "Pasta", "date": date(2026, 10, 12), "vegetarian": True,
             "categories": ["pasta"], "_complete": True, "_keys": {"nudeln"}, "total_minutes": 20},
            {"recipe_id": 2, "name": "Tiramisu", "date": date(2026, 10, 12), "vegetarian": True,
             "categories": ["dessert"], "_complete": True, "_keys": {"mascarpone"}, "total_minutes": 20, "meal_course": "dessert"}]
    assert _snapshot_warnings(criteria, rows, set()) == []
    assert any("Nachspeise fehlt" in warning for warning in _snapshot_warnings(criteria, rows[:1], set()))


def test_pdf_validates_course_against_server_recipe_and_exports_optional_slots(client, test_db):
    main = _recipe(test_db, "Reis", ("Reis",))
    dessert = _recipe(test_db, "Tiramisu", ("Mascarpone",))
    payload = {"criteria": {"count": 1, "week_start": "2026-10-12", "quota_mode": "minimum",
                            "additional_courses": [{"weekday": 1, "course": "dessert"}]},
               "items": [{"recipe_id": main, "date": "2026-10-12", "servings": 2},
                         {"recipe_id": dessert, "date": "2026-10-12", "servings": 2, "meal_course": "dessert"}]}
    response = client.post("/api/discovery/meal-plan/pdf", json=payload)
    assert response.status_code == 200 and response.content.startswith(b"%PDF")
    payload["items"][0]["recipe_id"], payload["items"][1]["recipe_id"] = dessert, main
    assert client.post("/api/discovery/meal-plan/pdf", json=payload).status_code == 409


def test_unknown_course_is_rejected(client):
    assert client.post("/api/discovery/meal-plan", json={"meal_course": "any"}).status_code == 422
