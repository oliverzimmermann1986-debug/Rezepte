"""Wiederkehrende Artikel benutzen dieselbe kanonische Einkaufsliste."""

from datetime import date, timedelta


def test_due_recurring_item_is_materialized_once_and_advanced(client, test_db):
    created = client.post(
        "/api/cart/recurring",
        json={
            "name": "Milch",
            "amount": 2,
            "default_unit": "l",
            "category": "Kühlregal",
            "interval_days": 7,
            "next_due_on": date.today().isoformat(),
        },
    )
    assert created.status_code == 200

    first = client.get("/api/cart")
    assert first.status_code == 200
    assert first.json()["recurring_added"] == 1
    assert len(first.json()["items"]) == 1
    assert first.json()["items"][0]["name"] == "Milch"
    assert first.json()["items"][0]["amount"] == 2
    assert first.json()["items"][0]["unit"] == "l"
    assert first.json()["items"][0]["category"] == "Kühlregal"

    second = client.get("/api/cart")
    assert second.json()["recurring_added"] == 0
    assert len(second.json()["items"]) == 1

    rules = client.get("/api/cart/recurring").json()["items"]
    assert len(rules) == 1
    assert rules[0]["due_in_days"] == 7
    assert rules[0]["default_unit"] == "l"


def test_recurring_crud_pause_and_manual_run(client):
    tomorrow = date.today() + timedelta(days=1)
    created = client.post(
        "/api/cart/recurring",
        json={
            "name": "Kaffee",
            "interval_days": 14,
            "next_due_on": tomorrow.isoformat(),
            "active": False,
        },
    )
    item_id = created.json()["id"]
    assert client.post("/api/cart/recurring/run", json={}).json()["count"] == 0

    changed = client.patch(
        f"/api/cart/recurring/{item_id}",
        json={
            "name": "Kaffeebohnen",
            "amount": 500,
            "default_unit": "g",
            "next_due_on": date.today().isoformat(),
            "active": True,
        },
    )
    assert changed.status_code == 200
    run = client.post("/api/cart/recurring/run", json={})
    assert run.status_code == 200
    assert run.json()["count"] == 1
    assert run.json()["added"][0]["name"] == "Kaffeebohnen"

    assert client.delete(f"/api/cart/recurring/{item_id}").status_code == 200
    assert client.delete(f"/api/cart/recurring/{item_id}").status_code == 404


def test_recurring_validation_rejects_empty_or_invalid_intervals(client):
    assert client.post(
        "/api/cart/recurring", json={"name": "", "interval_days": 7}
    ).status_code == 422
    assert client.post(
        "/api/cart/recurring", json={"name": "Milch", "interval_days": 0}
    ).status_code == 422
