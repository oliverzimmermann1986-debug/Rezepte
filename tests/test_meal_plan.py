"""Wochenplan: Planung, Portionsskalierung und gemeinsame Einkaufsliste."""

import random
from datetime import date, datetime
from io import BytesIO

import pdfplumber
import pytest

from app.db import Database
from app.recipes.meal_conductor import build_conductor_plan
from tests.conftest import _create_recipe


def _meal_recipe(
    db: Database,
    *,
    name: str,
    folder: str,
    servings: int,
    pasta_grams: float,
) -> int:
    recipe = _create_recipe(db, name=name, folder_path=folder)
    recipe_id = int(recipe["id"])
    db.recipe_set_extraction_result(
        recipe_id,
        "ok",
        [
            {
                "name": "Pasta",
                "canonical_name": "pasta",
                "amount": pasta_grams,
                "unit": "g",
            },
            {
                "name": "Salz",
                "canonical_name": "salz",
                "amount": 1,
                "unit": "Prise",
            },
        ],
    )
    db.recipe_set_servings(recipe_id, servings)
    return recipe_id


def _conductor_entries(count: int) -> list[dict]:
    return [
        {
            "recipe_id": recipe_id,
            "recipe_name": f"Gericht {recipe_id:02d}",
            "planned_servings": 2,
        }
        for recipe_id in range(1, count + 1)
    ]


def _build_test_plan(
    entries: list[dict],
    steps_by_recipe: dict[int, list[dict]],
    *,
    active_cooks: int = 1,
    burners: int = 4,
    oven_slots: int = 1,
    serve_hour: int = 19,
    serve_minute: int = 0,
) -> dict:
    return build_conductor_plan(
        entries,
        steps_by_recipe,
        planned_for=date(2026, 7, 27),
        serve_hour=serve_hour,
        serve_minute=serve_minute,
        active_cooks=active_cooks,
        burners=burners,
        oven_slots=oven_slots,
    )


def _assert_resource_capacity(plan: dict, resource: str, capacity: int) -> None:
    events = [event for event in plan["events"] if event["resource"] == resource]
    if not events:
        return
    starts = [datetime.fromisoformat(event["start_at"]) for event in events]
    ends = [datetime.fromisoformat(event["end_at"]) for event in events]
    boundaries = sorted(set(starts + ends))
    for left, right in zip(boundaries, boundaries[1:]):
        if left == right:
            continue
        concurrent = sum(
            start < right and end > left
            for start, end in zip(starts, ends)
        )
        assert concurrent <= capacity


def _auth_tokens(monkeypatch, test_db: Database) -> tuple[str, str]:
    from app import auth

    class Config:
        def get(self, *keys, default=None):
            values = {
                ("web",): {"auth_disabled": False},
                ("web", "secret_key"): "m" * 48,
            }
            return values.get(keys, default)

    monkeypatch.setattr(auth, "get_config", lambda: Config())
    test_db.user_create("anna", "unused-test-hash", role="user")
    return auth.create_session("anna"), auth.create_guest_session()


def _prepare_conductor_day(test_db: Database) -> int:
    recipe_id = _meal_recipe(
        test_db,
        name="Gastgericht",
        folder="/tmp/conductor-auth",
        servings=2,
        pasta_grams=100,
    )
    test_db.recipe_steps_set(
        recipe_id,
        [{"instruction": "Zwiebeln schneiden", "timer_seconds": 600}],
    )
    test_db.meal_plan_add(
        planned_for="2026-07-27",
        recipe_id=recipe_id,
        planned_servings=2,
    )
    return recipe_id


def test_week_normalizes_to_monday_and_aggregates_scaled_ingredients(
    client,
    test_db: Database,
):
    first = _meal_recipe(
        test_db,
        name="Pasta eins",
        folder="/tmp/meal-one",
        servings=2,
        pasta_grams=100,
    )
    second = _meal_recipe(
        test_db,
        name="Pasta zwei",
        folder="/tmp/meal-two",
        servings=4,
        pasta_grams=200,
    )
    test_db.shopping_exclusion_set("salz", True)

    one = client.post(
        "/api/meal-plan/items",
        json={
            "planned_for": "2026-07-27",
            "recipe_id": first,
            "planned_servings": 4,
        },
    )
    two = client.post(
        "/api/meal-plan/items",
        json={
            "planned_for": "2026-07-28",
            "recipe_id": second,
            "planned_servings": 2,
        },
    )
    assert one.status_code == 200
    assert two.status_code == 200

    response = client.get("/api/meal-plan?week_start=2026-07-29")
    assert response.status_code == 200
    week = response.json()
    assert week["week_start"] == "2026-07-27"
    assert week["week_end"] == "2026-08-02"
    assert len(week["days"]) == 7
    assert week["summary"] == {
        "planned_meals": 2,
        "planned_days": 2,
        "shopping_items": 1,
    }
    assert week["days"][0]["items"][0]["multiplier"] == 2
    assert week["days"][1]["items"][0]["multiplier"] == 0.5

    pasta = week["shopping_preview"][0]
    assert pasta["canonical_name"] == "nudeln"
    assert pasta["amount"] == 300
    assert pasta["unit"] == "g"
    assert pasta["source_recipe_ids"] == [first, second]


def test_update_delete_and_duplicate_day_recipe_are_deterministic(
    client,
    test_db: Database,
):
    recipe_id = _meal_recipe(
        test_db,
        name="Auflauf",
        folder="/tmp/meal-update",
        servings=2,
        pasta_grams=150,
    )
    payload = {
        "planned_for": "2026-07-27",
        "recipe_id": recipe_id,
        "planned_servings": 2,
    }
    created = client.post("/api/meal-plan/items", json=payload)
    item_id = created.json()["item"]["id"]

    payload["planned_servings"] = 5
    duplicate = client.post("/api/meal-plan/items", json=payload)
    assert duplicate.status_code == 200
    week = client.get("/api/meal-plan?week_start=2026-07-27").json()
    assert week["summary"]["planned_meals"] == 1
    assert week["days"][0]["items"][0]["planned_servings"] == 5

    updated = client.patch(
        f"/api/meal-plan/items/{item_id}",
        json={"planned_servings": 3},
    )
    assert updated.status_code == 200
    assert client.delete(f"/api/meal-plan/items/{item_id}").status_code == 200
    assert client.delete(f"/api/meal-plan/items/{item_id}").status_code == 404


def test_conductor_builds_resource_aware_timeline_without_persisting(
    client,
    test_db: Database,
):
    first = _meal_recipe(
        test_db,
        name="Ofengemuese",
        folder="/tmp/conductor-oven-one",
        servings=2,
        pasta_grams=100,
    )
    second = _meal_recipe(
        test_db,
        name="Auflauf",
        folder="/tmp/conductor-oven-two",
        servings=2,
        pasta_grams=100,
    )
    test_db.recipe_steps_set(first, [
        {"instruction": "Gemüse schneiden"},
        {"instruction": "Im Backofen garen", "timer_seconds": 1_800},
    ])
    test_db.recipe_steps_set(second, [
        {"instruction": "Sauce verrühren"},
        {"instruction": "Auflauf im Ofen backen", "timer_seconds": 1_200},
    ])
    for recipe_id in (first, second):
        response = client.post(
            "/api/meal-plan/items",
            json={
                "planned_for": "2026-07-27",
                "recipe_id": recipe_id,
                "planned_servings": 4,
            },
        )
        assert response.status_code == 200

    response = client.post(
        "/api/meal-plan/conductor/preview",
        json={
            "planned_for": "2026-07-27",
            "serve_at": "19:00",
            "burners": 2,
            "oven_slots": 1,
        },
    )

    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["serve_time"] == "19:00"
    assert plan["summary"]["recipes"] == 2
    assert plan["summary"]["steps"] == 4
    assert plan["summary"]["estimated_steps"] == 2
    assert plan["summary"]["resource_adjustments"] >= 1
    assert any("5 Minuten" in warning for warning in plan["warnings"])
    oven_events = [event for event in plan["events"] if event["resource"] == "oven"]
    assert len(oven_events) == 2
    assert (
        oven_events[0]["end_at"] <= oven_events[1]["start_at"]
        or oven_events[1]["end_at"] <= oven_events[0]["start_at"]
    )
    get_response = client.get(
        "/api/meal-plan/conductor/preview",
        params={
            "planned_for": "2026-07-27",
            "serve_at": "19:00",
            "burners": 2,
            "oven_slots": 1,
        },
    )
    assert get_response.status_code == 200
    assert get_response.json() == plan
    assert test_db.meal_plan_entries("2026-07-27", "2026-07-27")[0][
        "planned_servings"
    ] == 4


def test_conductor_requires_a_planned_recipe(client):
    response = client.post(
        "/api/meal-plan/conductor/preview",
        json={"planned_for": "2026-07-27", "serve_at": "19:00"},
    )

    assert response.status_code == 400
    assert "keine Gerichte" in response.json()["detail"]


def test_conductor_limits_manual_work_to_one_active_cook_by_default():
    entries = _conductor_entries(2)
    steps = {
        entry["recipe_id"]: [
            {
                "step_number": 1,
                "instruction": "Gemüse schneiden",
                "timer_seconds": 600,
            }
        ]
        for entry in entries
    }

    plan = _build_test_plan(entries, steps)

    _assert_resource_capacity(plan, "counter", 1)
    assert plan["summary"]["active_cooks"] == 1
    assert plan["summary"]["counter_adjustments"] == 1
    assert any("Kochkapazitaet" in warning for warning in plan["warnings"])
    counter_events = [event for event in plan["events"] if event["resource"] == "counter"]
    assert counter_events[0]["end_at"] <= counter_events[1]["start_at"]


def test_conductor_allows_matching_manual_parallelism_with_two_active_cooks():
    entries = _conductor_entries(2)
    steps = {
        entry["recipe_id"]: [
            {
                "step_number": 1,
                "instruction": "Salat schneiden",
                "timer_seconds": 600,
            }
        ]
        for entry in entries
    }

    plan = _build_test_plan(entries, steps, active_cooks=2)

    _assert_resource_capacity(plan, "counter", 2)
    counter_events = [event for event in plan["events"] if event["resource"] == "counter"]
    assert {event["start_time"] for event in counter_events} == {"18:50"}
    assert {event["end_time"] for event in counter_events} == {"19:00"}
    assert plan["summary"]["counter_adjustments"] == 0


@pytest.mark.parametrize(
    ("resource", "instruction"),
    [
        ("burner", "Im Topf kochen"),
        ("oven", "Im Backofen garen"),
    ],
)
def test_conductor_preserves_burner_and_oven_capacities(resource, instruction):
    entries = _conductor_entries(2)
    steps = {
        entry["recipe_id"]: [
            {
                "step_number": 1,
                "instruction": instruction,
                "timer_seconds": 600,
            }
        ]
        for entry in entries
    }

    single_kwargs = {"burners": 1} if resource == "burner" else {"oven_slots": 1}
    double_kwargs = {"burners": 2} if resource == "burner" else {"oven_slots": 2}
    single = _build_test_plan(entries, steps, **single_kwargs)
    double = _build_test_plan(entries, steps, **double_kwargs)

    _assert_resource_capacity(single, resource, 1)
    _assert_resource_capacity(double, resource, 2)
    single_events = [event for event in single["events"] if event["resource"] == resource]
    double_events = [event for event in double["events"] if event["resource"] == resource]
    assert single_events[0]["end_at"] <= single_events[1]["start_at"]
    assert {event["start_time"] for event in double_events} == {"18:50"}
    assert single["summary"]["device_adjustments"] == 1
    assert double["summary"]["device_adjustments"] == 0


def test_conductor_rejects_recipe_without_steps():
    with pytest.raises(ValueError, match="keine Zubereitungsschritte"):
        _build_test_plan(_conductor_entries(1), {1: []})


@pytest.mark.parametrize(
    "capacity_overrides",
    [
        {"active_cooks": 0},
        {"active_cooks": 9},
        {"burners": 0},
        {"burners": 9},
        {"oven_slots": 0},
        {"oven_slots": 5},
    ],
)
def test_conductor_core_rejects_invalid_capacities(capacity_overrides):
    steps = {
        1: [
            {
                "step_number": 1,
                "instruction": "Gemüse schneiden",
                "timer_seconds": 600,
            }
        ]
    }

    with pytest.raises(ValueError, match="zwischen 1 und"):
        _build_test_plan(_conductor_entries(1), steps, **capacity_overrides)


def test_conductor_supports_multi_day_plans_without_a_24_hour_cutoff():
    entries = _conductor_entries(2)
    steps = {
        entry["recipe_id"]: [
            {
                "step_number": 1,
                "instruction": "Teig von Hand bearbeiten",
                "timer_seconds": 26 * 60 * 60,
            }
        ]
        for entry in entries
    }

    plan = _build_test_plan(
        entries,
        steps,
        active_cooks=1,
        serve_hour=1,
    )

    assert plan["serve_at"] == "2026-07-27T01:00"
    assert plan["start_at"] == "2026-07-24T21:00"
    assert plan["summary"]["duration_minutes"] == 52 * 60
    assert plan["summary"]["starts_previous_day"] is True
    assert any("3 Tag(e) vor" in warning for warning in plan["warnings"])
    _assert_resource_capacity(plan, "counter", 1)
    assert all(
        datetime.fromisoformat(event["end_at"])
        <= datetime.fromisoformat(plan["serve_at"])
        for event in plan["events"]
    )


def test_conductor_order_is_deterministic_for_equal_named_recipes():
    entries = [
        {"recipe_id": 2, "recipe_name": "Gleich", "planned_servings": 2},
        {"recipe_id": 1, "recipe_name": "Gleich", "planned_servings": 2},
    ]
    steps = {
        recipe_id: [
            {
                "step_number": 1,
                "instruction": "Gemüse schneiden",
                "timer_seconds": 600,
            }
        ]
        for recipe_id in (1, 2)
    }

    forward = _build_test_plan(entries, steps)
    reverse = _build_test_plan(list(reversed(entries)), steps)

    assert forward == reverse
    assert [event["recipe_id"] for event in forward["events"]] == [2, 1]


@pytest.mark.parametrize(
    ("method", "field", "value"),
    [
        (method, field, value)
        for method in ("get", "post")
        for field, value in (
            ("active_cooks", 0),
            ("active_cooks", 9),
            ("burners", 0),
            ("burners", 9),
            ("oven_slots", 0),
            ("oven_slots", 5),
            ("serve_at", "25:61"),
        )
    ],
)
def test_conductor_preview_rejects_invalid_query_and_body_values(
    client,
    method,
    field,
    value,
):
    payload = {
        "planned_for": "2026-07-27",
        "serve_at": "19:00",
        "active_cooks": 1,
        "burners": 4,
        "oven_slots": 1,
    }
    payload[field] = value

    if method == "get":
        response = client.get("/api/meal-plan/conductor/preview", params=payload)
    else:
        response = client.post("/api/meal-plan/conductor/preview", json=payload)

    assert response.status_code == 422


def test_conductor_authenticated_post_remains_supported(
    client,
    test_db: Database,
    monkeypatch,
):
    from app import auth
    from app.main import app

    _prepare_conductor_day(test_db)
    user_token, _guest_token = _auth_tokens(monkeypatch, test_db)
    app.dependency_overrides.pop(auth.require_auth, None)
    try:
        response = client.post(
            "/api/meal-plan/conductor/preview",
            headers={"Authorization": f"Bearer {user_token}"},
            json={"planned_for": "2026-07-27", "serve_at": "19:00"},
        )
    finally:
        app.dependency_overrides[auth.require_auth] = lambda: None

    assert response.status_code == 200, response.text
    assert response.json()["summary"]["active_cooks"] == 1


def test_conductor_signed_guest_can_use_read_only_get_preview(
    client,
    test_db: Database,
    monkeypatch,
):
    from app import auth
    from app.main import app

    _prepare_conductor_day(test_db)
    _user_token, guest_token = _auth_tokens(monkeypatch, test_db)
    before = test_db.meal_plan_entries("2026-07-27", "2026-07-27")
    app.dependency_overrides.pop(auth.require_auth, None)
    try:
        anonymous_response = client.get(
            "/api/meal-plan/conductor/preview",
            params={"planned_for": "2026-07-27", "serve_at": "19:00"},
        )
        response = client.get(
            "/api/meal-plan/conductor/preview",
            headers={"Authorization": f"Bearer {guest_token}"},
            params={
                "planned_for": "2026-07-27",
                "serve_at": "19:00",
                "active_cooks": 2,
            },
        )
    finally:
        app.dependency_overrides[auth.require_auth] = lambda: None

    assert anonymous_response.status_code == 401
    assert response.status_code == 200, response.text
    assert response.json()["summary"]["active_cooks"] == 2
    assert test_db.meal_plan_entries("2026-07-27", "2026-07-27") == before


def test_conductor_signed_guest_post_remains_forbidden(
    client,
    test_db: Database,
    monkeypatch,
):
    from app import auth
    from app.main import app

    _prepare_conductor_day(test_db)
    _user_token, guest_token = _auth_tokens(monkeypatch, test_db)
    app.dependency_overrides.pop(auth.require_auth, None)
    try:
        preview_response = client.post(
            "/api/meal-plan/conductor/preview",
            headers={"Authorization": f"Bearer {guest_token}"},
            json={"planned_for": "2026-07-27", "serve_at": "19:00"},
        )
        other_mutations = [
            client.post(
                "/api/meal-plan/items",
                headers={"Authorization": f"Bearer {guest_token}"},
                json={
                    "planned_for": "2026-07-27",
                    "recipe_id": 1,
                    "planned_servings": 2,
                },
            ),
            client.patch(
                "/api/meal-plan/items/1",
                headers={"Authorization": f"Bearer {guest_token}"},
                json={"planned_servings": 3},
            ),
            client.delete(
                "/api/meal-plan/items/1",
                headers={"Authorization": f"Bearer {guest_token}"},
            ),
            client.post(
                "/api/meal-plan/cart",
                headers={"Authorization": f"Bearer {guest_token}"},
                json={"week_start": "2026-07-27"},
            ),
        ]
    finally:
        app.dependency_overrides[auth.require_auth] = lambda: None

    for response in [preview_response, *other_mutations]:
        assert response.status_code == 403
        assert response.json()["detail"] == "Der Gastzugang ist schreibgeschützt."


def test_conductor_500_randomized_schedules_preserve_invariants():
    rng = random.Random(20260830)
    instructions = (
        ("counter", "Zwiebeln schneiden"),
        ("burner", "Im Topf kochen"),
        ("oven", "Im Backofen garen"),
    )

    for _case in range(500):
        recipe_count = rng.randint(1, 4)
        entries = _conductor_entries(recipe_count)
        steps_by_recipe: dict[int, list[dict]] = {}
        for entry in entries:
            steps = []
            for step_number in range(1, rng.randint(1, 3) + 1):
                _resource, instruction = rng.choice(instructions)
                steps.append({
                    "step_number": step_number,
                    "instruction": instruction,
                    "timer_seconds": rng.randint(1, 8) * 60,
                })
            steps_by_recipe[entry["recipe_id"]] = steps

        active_cooks = rng.randint(1, 3)
        burners = rng.randint(1, 3)
        oven_slots = rng.randint(1, 3)
        plan = _build_test_plan(
            entries,
            steps_by_recipe,
            active_cooks=active_cooks,
            burners=burners,
            oven_slots=oven_slots,
        )
        repeated = _build_test_plan(
            list(reversed(entries)),
            steps_by_recipe,
            active_cooks=active_cooks,
            burners=burners,
            oven_slots=oven_slots,
        )

        assert plan == repeated
        assert len(plan["events"]) == sum(map(len, steps_by_recipe.values()))
        assert all(event["duration_minutes"] > 0 for event in plan["events"])
        serve_at = datetime.fromisoformat(plan["serve_at"])
        assert all(
            datetime.fromisoformat(event["start_at"])
            < datetime.fromisoformat(event["end_at"])
            <= serve_at
            for event in plan["events"]
        )
        for recipe_id in steps_by_recipe:
            recipe_events = sorted(
                (
                    event
                    for event in plan["events"]
                    if event["recipe_id"] == recipe_id
                ),
                key=lambda event: event["step_number"],
            )
            assert all(
                first["end_at"] <= second["start_at"]
                for first, second in zip(recipe_events, recipe_events[1:])
            )
        _assert_resource_capacity(plan, "counter", active_cooks)
        _assert_resource_capacity(plan, "burner", burners)
        _assert_resource_capacity(plan, "oven", oven_slots)


def test_week_cart_merges_into_the_one_local_list(
    client,
    test_db: Database,
):
    recipe_id = _meal_recipe(
        test_db,
        name="Wochenpasta",
        folder="/tmp/meal-cart",
        servings=2,
        pasta_grams=250,
    )
    client.post(
        "/api/meal-plan/items",
        json={
            "planned_for": "2026-07-27",
            "recipe_id": recipe_id,
            "planned_servings": 4,
        },
    )
    test_db.shopping_exclusion_set("salz", True)
    test_db.cart_add_or_merge(
        name="Alter Artikel",
        canonical_name="alt",
        amount=1,
        unit=None,
        source_recipe_id=None,
    )

    response = client.post(
        "/api/meal-plan/cart",
        json={"week_start": "2026-07-27"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "target": "local",
        "added": 1,
        "merged": 0,
        "replaced": False,
        "week_start": "2026-07-27",
    }
    cart = test_db.cart_list()
    assert len(cart) == 2
    by_canonical = {item["canonical_name"]: item for item in cart}
    assert by_canonical["alt"]["amount"] == 1
    assert by_canonical["nudeln"]["amount"] == 500
    assert by_canonical["nudeln"]["unit"] == "g"

    repeated = client.post(
        "/api/meal-plan/cart",
        json={"week_start": "2026-07-27"},
    )
    assert repeated.json()["added"] == 0
    assert repeated.json()["merged"] == 1
    by_canonical = {item["canonical_name"]: item for item in test_db.cart_list()}
    assert by_canonical["nudeln"]["amount"] == 1000


def test_meal_plan_rejects_invalid_input(client):
    assert client.get("/api/meal-plan?week_start=kein-datum").status_code == 422
    assert client.post(
        "/api/meal-plan/items",
        json={
            "planned_for": "2026-07-27",
            "recipe_id": 999999,
            "planned_servings": 2,
        },
    ).status_code == 404
    assert client.post(
        "/api/meal-plan/items",
        json={
            "planned_for": "2026-07-27",
            "recipe_id": 1,
            "planned_servings": 0,
        },
    ).status_code == 422


def test_week_pdf_contains_plan_and_shopping_list(client, test_db: Database):
    recipe_id = _meal_recipe(
        test_db,
        name="PDF Zitronenpasta",
        folder="/tmp/meal-pdf",
        servings=2,
        pasta_grams=240,
    )
    test_db.shopping_exclusion_set("salz", True)
    client.post(
        "/api/meal-plan/items",
        json={
            "planned_for": "2026-07-27",
            "recipe_id": recipe_id,
            "planned_servings": 3,
        },
    )

    response = client.get("/api/meal-plan/pdf?week_start=2026-07-27")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["content-disposition"] == (
        'attachment; filename="wochenplan-2026-07-27.pdf"'
    )
    assert response.content.startswith(b"%PDF-")

    with pdfplumber.open(BytesIO(response.content)) as document:
        text = "\n".join(page.extract_text() or "" for page in document.pages)
    assert "Gemeinsam planen" in text
    assert "PDF Zitronenpasta" in text
    assert "Gemeinsame Einkaufsliste" in text
    assert "360 g Pasta" in text
    assert "Salz" not in text
