import json
import time

from app.core.analyzer import OpenAIAnalyzer


class _AiConfig:
    def get(self, *keys, default=None):
        if keys == ("ai",):
            return {"openai": {"api_key": "test"}}
        return default


class _FakeShoppingAnalyzer:
    def optimize_shopping_list(self, items):
        names = {int(item["id"]): item["name"] for item in items}
        return [
            {"id": item_id, "name": "Kartoffeln", "category": "Obst & Gemüse"}
            if name in {"Kartoffel", "Kartoffeln"}
            else {"id": item_id, "name": name, "category": "Kühlregal"}
            for item_id, name in names.items()
        ]


def _insert_cart_item(db, *, name, canonical, amount, unit="Stück", checked=0, source_ids=None):
    with db.conn() as connection:
        cursor = connection.execute(
            "INSERT INTO shopping_cart "
            "(name, canonical_name, amount, unit, checked, added_at, source_recipe_ids) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                name,
                canonical,
                amount,
                unit,
                checked,
                time.time(),
                json.dumps(source_ids or []),
            ),
        )
        return int(cursor.lastrowid)


def _configure_fake_ai(monkeypatch):
    from app.routes import api_shopping

    monkeypatch.setattr(api_shopping, "get_config", lambda: _AiConfig())
    monkeypatch.setattr(api_shopping, "build_analyzer", lambda _config: _FakeShoppingAnalyzer())


def test_ai_cart_preview_and_apply_preserve_amounts_and_sources(
    client, test_db, monkeypatch
):
    _configure_fake_ai(monkeypatch)
    first_id = _insert_cart_item(
        test_db, name="Kartoffel", canonical="kartoffel", amount=2, source_ids=[11]
    )
    second_id = _insert_cart_item(
        test_db, name="Kartoffeln", canonical="kartoffeln", amount=3, source_ids=[12]
    )

    preview_response = client.post("/api/cart/optimize/preview")

    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["summary"] == {
        "original_count": 2,
        "optimized_count": 1,
        "merged_count": 1,
        "renamed_count": 1,
        "categorized_count": 2,
    }
    assert preview["items"][0]["name"] == "Kartoffeln"
    assert preview["items"][0]["amount"] == 5
    assert preview["items"][0]["unit"] == "Stück"
    assert preview["items"][0]["category"] == "Obst & Gemüse"
    assert preview["items"][0]["source_item_ids"] == sorted([first_id, second_id])
    assert len(test_db.cart_list()) == 2  # Vorschau verändert die Liste nicht.

    apply_response = client.post(
        "/api/cart/optimize/apply",
        json={"preview_id": preview["preview_id"]},
    )

    assert apply_response.status_code == 200, apply_response.text
    stored = test_db.cart_list()
    assert len(stored) == 1
    assert stored[0]["name"] == "Kartoffeln"
    assert stored[0]["amount"] == 5
    assert stored[0]["unit"] == "Stück"
    assert stored[0]["category"] == "Obst & Gemüse"
    assert json.loads(stored[0]["source_recipe_ids"]) == [11, 12]


def test_ai_cart_apply_rejects_a_list_changed_after_preview(
    client, test_db, monkeypatch
):
    _configure_fake_ai(monkeypatch)
    _insert_cart_item(test_db, name="Kartoffel", canonical="kartoffel", amount=2)
    preview = client.post("/api/cart/optimize/preview").json()

    added = client.post("/api/cart/add", json={"name": "Milch"})
    assert added.status_code == 200
    response = client.post(
        "/api/cart/optimize/apply",
        json={"preview_id": preview["preview_id"]},
    )

    assert response.status_code == 409
    assert {item["name"] for item in test_db.cart_list()} == {"Kartoffel", "Milch"}


def test_shopping_ai_receives_names_but_no_amounts_or_units(monkeypatch):
    analyzer = object.__new__(OpenAIAnalyzer)
    captured = {}

    def fake_call(system, user):
        captured["system"] = system
        captured["user"] = user
        return '{"items":[{"id":7,"name":"Milch","category":"Kühlregal"}]}'

    monkeypatch.setattr(analyzer, "_call", fake_call)
    result = analyzer.optimize_shopping_list([
        {"id": 7, "name": "milch", "amount": 2, "unit": "l"},
    ])

    assert result == [{"id": 7, "name": "Milch", "category": "Kühlregal"}]
    assert '"amount"' not in captured["user"]
    assert '"unit"' not in captured["user"]
    assert "Mengen sind nicht Teil" in captured["system"]


def test_ai_cart_does_not_merge_unknown_and_explicit_amounts():
    from app.recipes.shopping_optimizer import build_optimized_cart

    items = [
        {
            "id": 1, "name": "Milch", "canonical_name": "milch",
            "amount": None, "unit": "ml", "checked": 0, "added_at": 1,
            "source_recipe_ids": "[]", "category": None, "sort_order": None,
        },
        {
            "id": 2, "name": "Milch", "canonical_name": "milch",
            "amount": 500, "unit": "ml", "checked": 0, "added_at": 2,
            "source_recipe_ids": "[]", "category": None, "sort_order": None,
        },
    ]
    suggestions = [
        {"id": 1, "name": "Milch", "category": "Kühlregal"},
        {"id": 2, "name": "Milch", "category": "Kühlregal"},
        "ungültig",
    ]

    optimized = build_optimized_cart(items, suggestions)

    assert optimized["matched_suggestions"] == 2
    assert optimized["summary"]["merged_count"] == 0
    assert len(optimized["items"]) == 2
