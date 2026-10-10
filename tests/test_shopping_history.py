"""Historical recipe splits and loss-safe undo across retries and households."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest

from app.db import CURRENT_SCHEMA_VERSION, Database
from app.recipes.cart_logic import add_recipe_to_cart, aggregate_recipes_for_cart, cart_for_display
from app.recipes.discovery import add_missing_to_cart
from app.recipes.shopping_optimizer import build_optimized_cart, cart_fingerprint
from app.tenant_db import HouseholdDatabase
from app.tenancy import HouseholdScope, merge_households
from tests.conftest import _create_recipe


def recipe(db, name, amount, unit="g"):
    result = _create_recipe(db, name=name, folder_path="/history-test/" + name)
    rid = result["id"]
    db.recipe_set_extraction_result(rid, "ok", [{"name": "Pasta", "canonical_name": "pasta", "amount": amount, "unit": unit}])
    db.recipe_set_servings(rid, 2)
    db.recipe_steps_set(rid, [{"instruction": "Nudeln kochen."}])
    return rid


def scoped(db, account=101):
    return HouseholdDatabase(db, HouseholdScope(account))


def add(db, suffix="001", **extra):
    return db.cart_sync([{"kind": "add", "operation_id": "history-add-" + suffix,
                         "name": "Milch", "amount": 2, "unit": "l", **extra}])[0]


def delete(db, suffix="001", item=None):
    item = item or cart_for_display(db)[0]
    operation = {"kind": "delete", "operation_id": "history-delete-" + suffix, "item_id": item["id"],
                 "expected_revision": item["sync_revision"], "expected_checked": item["checked"]}
    return operation, db.cart_sync([operation])[0]


def restore(target="history-delete-001", suffix="001"):
    return {"kind": "restore", "operation_id": "history-restore-" + suffix, "target_operation_id": target}


@pytest.mark.parametrize("household", [False, True])
def test_exact_contributions_scale_convert_merge_and_survive_recipe_edits(test_db, household):
    first = recipe(test_db, "Pasta eins", .2, "kg")
    second = recipe(test_db, "Pasta zwei", 300)
    db = scoped(test_db) if household else test_db
    add_recipe_to_cart(db, first, 2)
    add_recipe_to_cart(db, second)
    db.cart_add_or_merge(name="Pasta", canonical_name="pasta", amount=100, unit="g", source_recipe_id=None)
    test_db.recipe_set_extraction_result(first, "ok", [{"name": "Pasta", "amount": 999, "unit": "g"}])
    item = cart_for_display(db)[0]
    assert item["amount"] == 800
    assert item["source_contributions"] == [
        {"recipe_id": first, "recipe_name": "Pasta eins", "amount": 400, "unit": "g"},
        {"recipe_id": second, "recipe_name": "Pasta zwei", "amount": 300, "unit": "g"},
        {"recipe_id": None, "recipe_name": "Manuell hinzugefügt", "amount": 100, "unit": "g"},
    ]


def test_week_and_discovery_preserve_exact_scaled_split(test_db):
    first = recipe(test_db, "Woche", 200)
    second = recipe(test_db, "Zutaten", 300)
    db = scoped(test_db)
    items = aggregate_recipes_for_cart(db, [{"recipe_id": first, "multiplier": 2}, {"recipe_id": second, "multiplier": .5}])
    db.cart_merge_many(items)
    add_missing_to_cart(db, second, [], servings=4, request_id="missing-receipt")
    assert cart_for_display(db)[0]["amount_base"] == 1150
    assert [source["amount"] for source in cart_for_display(db)[0]["source_contributions"]] == [400, 750]
    add_missing_to_cart(db, second, [], servings=4, request_id="missing-receipt")
    assert cart_for_display(db)[0]["amount_base"] == 1150


def test_optimizer_keeps_contributions_when_renaming_and_replacing(test_db):
    rid = recipe(test_db, "Suppe", 100)
    db = scoped(test_db)
    add_recipe_to_cart(db, rid)
    db.cart_add_or_merge(name="Andere Nudeln", canonical_name="andere nudeln", amount=250, unit="g", source_recipe_id=None)
    old = db.cart_list()
    optimized = build_optimized_cart(old, [{"id": row["id"], "name": "Pasta", "category": "Sonstiges"} for row in old])
    assert db.cart_replace_if_unchanged(optimized["items"], cart_fingerprint(old)) == 1
    shown = cart_for_display(db)[0]
    assert shown["amount"] == 350
    assert sorted(source["amount"] for source in shown["source_contributions"]) == [100, 250]


def test_legacy_split_unknown_and_quantity_edit_invalidates_split(test_db):
    rid = recipe(test_db, "Alt", 200)
    db = scoped(test_db)
    add_recipe_to_cart(db, rid)
    row = db.cart_list()[0]
    db.cart_update(row["id"], amount=999)
    sources = cart_for_display(db)[0]["source_contributions"]
    assert sources == [{"recipe_id": rid, "recipe_name": "Alt", "amount": None, "unit": "g"}]
    db.cart_add_or_merge(name="Pasta", canonical_name="pasta", amount=25, unit="g", source_recipe_id=None)
    sources = cart_for_display(db)[0]["source_contributions"]
    assert sources[0]["amount"] is None and sources[1]["amount"] == 25


def test_deleted_or_foreign_private_recipe_name_is_never_returned(test_db):
    rid = recipe(test_db, "Geheime Suppe", 200)
    db = scoped(test_db)
    add_recipe_to_cart(db, rid)
    with test_db.conn() as c:
        c.execute("UPDATE recipes SET owner_account_id=202 WHERE id=?", (rid,))
    for view in (db, test_db):
        assert "Geheime Suppe" not in json.dumps(cart_for_display(view))
        assert cart_for_display(view)[0]["source_contributions"][0]["recipe_id"] is None
    with test_db.conn() as c:
        c.execute("UPDATE recipes SET owner_account_id=NULL,deleted_at=1 WHERE id=?", (rid,))
    assert "Geheime Suppe" not in json.dumps(cart_for_display(db))


def test_recurring_add_preserves_existing_recipe_part(test_db):
    rid = recipe(test_db, "Vorrat", 200)
    add_recipe_to_cart(test_db, rid)
    test_db.recurring_create(name="Pasta", canonical_name="pasta", amount=100, unit="g",
                             category=None, interval_days=7, next_due_on="2026-10-10", active=True)
    test_db.recurring_run_due(due_on=date(2026, 10, 10))
    assert [source["amount"] for source in cart_for_display(test_db)[0]["source_contributions"]] == [200, 100]


def test_household_merge_combines_history_without_recalculating(test_db):
    rid = recipe(test_db, "Merge", 200)
    first, second = scoped(test_db, 101), scoped(test_db, 202)
    add_recipe_to_cart(first, rid)
    add_recipe_to_cart(second, rid, 2)
    with test_db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        merge_households(c, 101, 202)
    assert not first.cart_list()
    assert cart_for_display(second)[0]["source_contributions"][0]["amount"] == 600


def test_delete_and_restore_lost_responses_are_exactly_once(test_db):
    db = scoped(test_db)
    rid = recipe(test_db, "Undo", 250)
    add_recipe_to_cart(db, rid)
    item = cart_for_display(db)[0]
    db.cart_update(item["id"], checked=True)
    before = db.cart_list()[0]
    operation, result = delete(db)
    assert result["status"] == "applied" and not db.cart_list()
    assert db.cart_sync([operation]) == [result]
    undone = db.cart_sync([restore()])
    assert undone[0]["status"] == "applied"
    after = db.cart_list()[0]
    assert after["id"] != before["id"]
    assert {k: v for k, v in after.items() if k != "id"} == {k: v for k, v in before.items() if k != "id"}
    assert scoped(test_db).cart_sync([restore()]) == undone
    assert len(db.cart_list()) == 1
    assert db.cart_sync([restore(suffix="002")])[0]["reason"] == "already_restored"


def test_restore_rejects_new_same_product_without_overwriting(test_db):
    db = scoped(test_db)
    add(db)
    delete(db)
    add(db, "002", amount=3)
    result = db.cart_sync([restore()])
    assert result[0]["reason"] == "item_changed"
    assert db.cart_list()[0]["amount"] == 3000
    db.cart_clear()
    assert db.cart_sync([restore()]) == result  # Conflict remains a stable receipt.
    assert db.cart_sync([restore(suffix="retry")])[0]["status"] == "applied"
    assert db.cart_list()[0]["amount"] == 2000


def test_partial_clear_undo_restores_only_deleted_rows(test_db):
    db = scoped(test_db)
    add(db)
    add(db, "bread", name="Brot", amount=1, unit="Stück")
    before = cart_for_display(db)
    changed = before[0]
    db.cart_update(changed["id"], amount=4)
    deleted = [delete(db, str(index), item)[1] for index, item in enumerate(before)]
    assert sorted(value["status"] for value in deleted) == ["applied", "conflict"]
    undone = db.cart_sync([restore("history-delete-" + str(index), str(index)) for index in range(2)])
    assert sorted(value["status"] for value in undone) == ["applied", "conflict"]
    assert len(db.cart_list()) == 2
    assert next(row for row in db.cart_list() if row["id"] == changed["id"])["amount"] == 4


def test_restore_scope_permissions_and_untrusted_payload(client, test_db, monkeypatch):
    from app.routes import api_shopping
    db = scoped(test_db)
    add(db)
    delete(db)
    assert scoped(test_db, 202).cart_sync([restore()])[0]["reason"] == "delete_missing"
    monkeypatch.setattr(api_shopping, "get_db", lambda: db)
    # Caller-supplied snapshot fields cannot alter the server's snapshot.
    response = client.post("/api/cart/sync", json={"household_id": 101, "operations": [{**restore(), "amount": 999, "name": "Falsch"}]})
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["amount"] == 2
    monkeypatch.setattr(api_shopping, "get_db", lambda: HouseholdDatabase(test_db, HouseholdScope(101, is_guest=True)))
    assert client.post("/api/cart/sync", json={"household_id": 101, "operations": [restore()]}).status_code == 403


def test_add_delete_restore_and_check_in_one_ordered_batch(test_db):
    db = scoped(test_db)
    operations = [
        {"kind": "add", "operation_id": "history-add-batch", "name": "Brot"},
        {"kind": "delete", "operation_id": "history-delete-batch", "target_operation_id": "history-add-batch", "expected_checked": False},
        restore("history-delete-batch", "batch"),
        {"kind": "check", "operation_id": "history-check-batch", "target_operation_id": "history-restore-batch", "expected_checked": False, "checked": True},
    ]
    assert all(result["status"] == "applied" for result in db.cart_sync(operations))
    assert db.cart_list()[0]["checked"] == 1
    assert all(result["status"] == "applied" for result in db.cart_sync(operations))
    assert len(db.cart_list()) == 1


def test_parallel_restore_is_exactly_once(test_db):
    db = scoped(test_db)
    add(db)
    delete(db)
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(lambda _: scoped(test_db).cart_sync([restore()]), range(3)))
    assert all(result == results[0] for result in results)
    assert len(db.cart_list()) == 1


def test_delete_snapshot_rolls_back_with_failed_receipt_batch(test_db):
    db = scoped(test_db)
    add(db)
    item = cart_for_display(db)[0]
    operation = {"kind": "delete", "operation_id": "history-delete-rollback", "item_id": item["id"],
                 "expected_revision": item["sync_revision"], "expected_checked": False}
    with pytest.raises(ValueError):
        db.cart_sync([operation, {"kind": "add", "operation_id": "history-add-001", "name": "Andere Milch"}])
    assert len(db.cart_list()) == 1
    assert db.cart_sync([restore(operation["operation_id"], "rollback")])[0]["reason"] == "delete_missing"


def test_migration_from_real_270_preserves_rows_receipts_and_backup(tmp_path):
    path = tmp_path / "shopping.db"
    db = Database(path)
    add(scoped(db))
    with db.conn() as c:
        c.execute("DROP TRIGGER delete_shopping_history")
        c.execute("DROP TABLE shopping_deleted_items")
        c.execute("ALTER TABLE shopping_cart DROP COLUMN source_contributions")
        c.execute("DELETE FROM schema_migrations WHERE version>270")
    migrated = Database(path)
    with migrated.conn() as c:
        assert c.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == CURRENT_SCHEMA_VERSION
    backup = next((tmp_path / "backups").glob(f"pre-migration-v270-to-v{CURRENT_SCHEMA_VERSION}-*.db"))
    with sqlite3.connect(backup) as c:
        assert c.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 270
        assert "source_contributions" not in {row[1] for row in c.execute("PRAGMA table_info(shopping_cart)")}
    shown = cart_for_display(scoped(migrated))[0]
    assert shown["amount"] == 2
    assert shown["source_contributions"][0]["amount"] is None
    add(scoped(migrated))  # Existing receipt still prevents duplicate merge.
    assert scoped(migrated).cart_list()[0]["amount"] == 2000
