"""Linked weeks reconcile only their owned quantities, atomically."""
import json

import pytest

from app.recipes.meal_plan_shopping import journal_key
from app.tenant_db import HouseholdDatabase
from app.tenancy import HouseholdScope
from tests.test_meal_plan import _meal_recipe
from tests.test_discovery import discovery_accounts  # noqa: F401 -- shared auth fixture


WEEK = "2026-10-12"


def recipe(db, name, amount=100):
    return _meal_recipe(db, name=name, folder=f"/linked/{name}", servings=2, pasta_grams=amount)


def add(client, rid, day=WEEK, servings=2):
    response = client.post("/api/meal-plan/items", json={"recipe_id": rid, "planned_for": day, "planned_servings": servings})
    assert response.status_code == 200, response.text
    return response.json()["item"]


def link(client, day=WEEK):
    response = client.post("/api/meal-plan/cart", json={"week_start": day})
    assert response.status_code == 200, response.text
    return response.json()


def pasta(db):
    return next(row for row in db.cart_list() if row["canonical_name"] == "nudeln")


def snapshot(db):
    with db.conn() as c:
        return {table: [tuple(row) for row in c.execute(f"SELECT * FROM {table} ORDER BY 1")]
                for table in ("meal_plan_entries", "shopping_cart", "shopping_sync_operations")}


def test_portions_reconcile_manual_quantities_and_checked_state(client, test_db):
    scoped = HouseholdDatabase(test_db, HouseholdScope(0))
    manual = scoped.cart_add_or_merge(name="Pasta", canonical_name="nudeln", amount=50, unit="g", source_recipe_id=None)
    scoped.cart_add_or_merge(name="Milch", canonical_name="milch", amount=1, unit="l", source_recipe_id=None)
    entry = add(client, recipe(test_db, "Pasta"))
    assert client.get(f"/api/meal-plan?week_start={WEEK}").json()["shopping_linked"] is False
    link(client)
    assert pasta(test_db)["amount"] == 150
    assert client.get(f"/api/meal-plan?week_start={WEEK}").json()["shopping_linked"] is True
    scoped.cart_update(manual, checked=True)
    assert link(client)["merged"] == 0
    assert pasta(test_db)["checked"] == 1
    assert client.patch(f"/api/meal-plan/items/{entry['id']}", json={"planned_servings": 4}).status_code == 200
    assert pasta(test_db)["amount"] == 250 and pasta(test_db)["checked"] == 0
    scoped.cart_update(manual, checked=True)
    assert client.patch(f"/api/meal-plan/items/{entry['id']}", json={"planned_servings": 2}).status_code == 200
    assert pasta(test_db)["amount"] == 150 and pasta(test_db)["checked"] == 1
    assert client.delete(f"/api/meal-plan/items/{entry['id']}").status_code == 200
    assert pasta(test_db)["amount"] == 50 and pasta(test_db)["checked"] == 1
    assert {row["canonical_name"] for row in test_db.cart_list()} == {"nudeln", "milch"}


@pytest.mark.parametrize("edit", ["delete", "amount"])
def test_manual_delete_and_free_amount_override_remain_respected(client, test_db, edit):
    scoped = HouseholdDatabase(test_db, HouseholdScope(0))
    entry = add(client, recipe(test_db, "Override"))
    link(client)
    cart_id = pasta(test_db)["id"]
    if edit == "delete":
        scoped.cart_delete(cart_id)
    else:
        scoped.cart_update(cart_id, amount=123, checked=True)
    for servings in (4, 6):
        assert client.patch(f"/api/meal-plan/items/{entry['id']}", json={"planned_servings": servings}).status_code == 200
        link(client)
        if edit == "delete":
            assert not any(row["canonical_name"] == "nudeln" for row in test_db.cart_list())
        else:
            assert pasta(test_db)["amount"] == 123 and pasta(test_db)["checked"] == 1


def test_move_between_linked_weeks_does_not_duplicate_or_remove_other_week(client, test_db):
    rid = recipe(test_db, "TwoWeeks")
    first = add(client, rid, servings=2)
    second = add(client, rid, day="2026-10-19", servings=4)
    link(client); link(client, "2026-10-19")
    assert pasta(test_db)["amount"] == 300
    assert client.patch(f"/api/meal-plan/items/{first['id']}", json={"planned_for": "2026-10-20"}).status_code == 200
    assert pasta(test_db)["amount"] == 300
    history = json.loads(pasta(test_db)["source_contributions"])
    assert {item.get("plan_week") for item in history} == {"2026-10-19"}
    assert client.delete(f"/api/meal-plan/items/{second['id']}").status_code == 200
    assert pasta(test_db)["amount"] == 100
    assert client.delete(f"/api/meal-plan/items/{first['id']}").status_code == 200
    assert test_db.cart_list() == []


def test_replace_retains_day_and_id_reconciles_cart_and_is_retry_safe(client, test_db):
    old, new = recipe(test_db, "Old", 100), recipe(test_db, "New", 250)
    entry = add(client, old, day="2026-10-15", servings=4)
    link(client)
    body = {"recipe_id": new, "expected_recipe_id": old, "planned_servings": 6}
    response = client.post(f"/api/meal-plan/items/{entry['id']}/replace", json=body)
    assert response.status_code == 200, response.text
    result = response.json()["item"]
    assert result["id"] == entry["id"] and result["planned_for"] == "2026-10-15"
    assert result["recipe_id"] == new and result["planned_servings"] == 6
    assert pasta(test_db)["amount"] == 750
    before = snapshot(test_db)
    assert client.post(f"/api/meal-plan/items/{entry['id']}/replace", json=body).json() == response.json()
    assert snapshot(test_db) == before
    stale = client.post(f"/api/meal-plan/items/{entry['id']}/replace", json={**body, "recipe_id": old})
    assert stale.status_code == 409
    assert snapshot(test_db) == before


def test_duplicate_replacement_and_sync_failure_roll_back_whole_transaction(client, test_db, monkeypatch):
    old, new = recipe(test_db, "RollbackOld"), recipe(test_db, "RollbackNew")
    entry = add(client, old); add(client, new)
    link(client)
    before = snapshot(test_db)
    body = {"recipe_id": new, "expected_recipe_id": old, "planned_servings": 2}
    assert client.post(f"/api/meal-plan/items/{entry['id']}/replace", json=body).status_code == 409
    assert snapshot(test_db) == before
    other = recipe(test_db, "RollbackOther")
    from app.recipes import meal_plan_shopping
    original = meal_plan_shopping.sync_dates

    def fail_after_write(*args):
        original(*args)
        raise ValueError("simulated reconciliation failure")

    monkeypatch.setattr(meal_plan_shopping, "sync_dates", fail_after_write)
    assert client.post(f"/api/meal-plan/items/{entry['id']}/replace", json={**body, "recipe_id": other}).status_code == 409
    assert snapshot(test_db) == before


def test_unlinked_weeks_do_not_change_cart_and_selected_date_is_authoritative(client, test_db):
    entry = add(client, recipe(test_db, "SelectedDay"), day="2026-10-25")
    assert test_db.cart_list() == []
    days = client.get("/api/meal-plan?week_start=2026-10-25").json()["days"]
    assert [day["date"] for day in days if day["items"]] == ["2026-10-25"]
    assert days[-1]["items"][0]["id"] == entry["id"]


def test_household_replacement_and_journal_are_scoped(client, test_db, discovery_accounts):
    users, login = discovery_accounts
    rid = recipe(test_db, "Scoped")
    login("anna-discovery")
    entry = add(client, rid); link(client)
    before = snapshot(test_db)
    login("bert-discovery")
    assert client.get(f"/api/meal-plan?week_start={WEEK}").json()["shopping_linked"] is False
    assert client.post(f"/api/meal-plan/items/{entry['id']}/replace", json={"recipe_id": rid, "expected_recipe_id": rid, "planned_servings": 3}).status_code == 404
    assert client.patch(f"/api/meal-plan/items/{entry['id']}", json={"planned_servings": 3}).status_code == 404
    assert client.delete(f"/api/meal-plan/items/{entry['id']}").status_code == 404
    assert snapshot(test_db) == before
    with test_db.conn() as c:
        assert c.execute("SELECT account_id FROM shopping_sync_operations WHERE operation_id=?", (journal_key(WEEK),)).fetchone()[0] == users["anna-discovery"]


def test_public_operation_cannot_collide_with_week_journal():
    from app.routes.api_shopping import SyncAdd
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        SyncAdd(operation_id=journal_key(WEEK), kind="add", name="Pasta")


@pytest.mark.parametrize("target_linked", [False, True])
def test_household_join_transfers_links_and_preserves_owned_quantities(test_db, target_linked):
    from app.recipes.meal_plan_shopping import sync_week, linked
    from app.tenancy import merge_households
    source, target = HouseholdDatabase(test_db, HouseholdScope(101)), HouseholdDatabase(test_db, HouseholdScope(202))
    rid = recipe(test_db, "Join")
    source.meal_plan_add(planned_for=WEEK, recipe_id=rid, planned_servings=2)
    target.meal_plan_add(planned_for=WEEK, recipe_id=rid, planned_servings=4)
    with test_db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        sync_week(c, source, 101, WEEK, enable=True)
        if target_linked: sync_week(c, target, 202, WEEK, enable=True)
    with test_db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        merge_households(c, 101, 202, db=test_db)
        assert linked(c, 202, WEEK) and not linked(c, 101, WEEK)
    assert pasta(target)["amount"] == 300
    entry = target.meal_plan_entries(WEEK, WEEK)[0]
    assert entry["planned_servings"] == 6
    target.meal_plan_update(entry["id"], planned_servings=8)
    assert pasta(target)["amount"] == 400


def test_unknown_split_does_not_double_known_subtotal(client, test_db):
    rid = recipe(test_db, "UnknownSplit")
    test_db.recipe_set_extraction_result(rid, "ok", [
        {"name": "Pasta", "canonical_name": "nudeln", "amount": 100, "unit": "g"},
        {"name": "Pasta", "canonical_name": "nudeln", "amount": None, "unit": "g"},
    ])
    entry = add(client, rid); link(client)
    assert pasta(test_db)["amount"] == 100
    assert client.patch(f"/api/meal-plan/items/{entry['id']}", json={"planned_servings": 4}).status_code == 200
    assert pasta(test_db)["amount"] == 200
