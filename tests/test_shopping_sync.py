"""Offline shopping receipts must survive retries, conflicts and tenant changes."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.tenant_db import HouseholdDatabase
from app.tenancy import HouseholdScope
from app.recipes.cart_logic import cart_content_revision


def add(operation_id="operation-add-0001", **extra):
    return {"operation_id": operation_id, "kind": "add", "name": "Milch", "amount": 2, "unit": "l", **extra}


def household(db, account=101):
    return HouseholdDatabase(db, HouseholdScope(account))


def test_retry_after_commit_does_not_merge_amount_twice(test_db):
    db = household(test_db)
    first = db.cart_sync([add()])
    assert db.cart_sync([add()]) == first
    items = db.cart_list()
    assert len(items) == 1
    assert items[0]["amount"] == 2000
    assert household(test_db).cart_sync([add()]) == first  # New connection/instance.


def test_ordered_add_check_uncheck_delete_resolves_local_reference(test_db):
    db = household(test_db)
    operations = [add()]
    for index, checked in enumerate([True, False, True]):
        operations.append({"operation_id": f"operation-check-{index}", "kind": "check",
                           "target_operation_id": operations[0]["operation_id"],
                           "after_operation_id": operations[-1]["operation_id"],
                           "expected_checked": not checked, "checked": checked})
    operations.append({"operation_id": "operation-delete-1", "kind": "delete",
                       "target_operation_id": operations[0]["operation_id"],
                       "after_operation_id": operations[-1]["operation_id"], "expected_checked": True})
    first = db.cart_sync(operations)
    assert all(item["status"] == "applied" for item in first)
    assert not db.cart_list()
    assert db.cart_sync(operations) == first
    assert not db.cart_list()


def test_conflicts_and_dependent_edits_are_explicit_and_stable(test_db):
    db = household(test_db)
    item_id = db.cart_sync([add()])[0]["item_id"]
    db.cart_update(item_id, checked=True)
    revision = cart_content_revision(db.cart_list()[0])
    check = {"operation_id": "operation-check-1", "kind": "check", "item_id": item_id,
             "expected_checked": False, "checked": True, "expected_revision": revision}
    delete = {"operation_id": "operation-delete-1", "kind": "delete", "item_id": item_id,
              "expected_checked": True, "after_operation_id": check["operation_id"], "expected_revision": revision}
    result = db.cart_sync([check, delete])
    assert [item["reason"] for item in result] == ["item_changed", "dependency_failed"]
    db.cart_update(item_id, checked=False)
    assert db.cart_sync([check, delete]) == result
    assert len(db.cart_list()) == 1
    db.cart_delete(item_id)
    missing = {**check, "operation_id": "operation-missing-1"}
    assert db.cart_sync([missing])[0]["reason"] == "item_missing"


def test_batch_receipts_and_changes_roll_back_together(test_db):
    db = household(test_db)
    db.cart_sync([add()])
    with pytest.raises(ValueError):
        db.cart_sync([add("operation-new-0001", name="Brot"), add(name="Andere Milch")])
    assert [item["name"] for item in db.cart_list()] == ["Milch"]
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM shopping_sync_operations").fetchone()[0] == 1


def test_parallel_duplicate_requests_are_exactly_once(test_db):
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: household(test_db).cart_sync([add()]), range(4)))
    assert all(result == results[0] for result in results)
    assert household(test_db).cart_list()[0]["amount"] == 2000


@pytest.mark.parametrize("kind", ["check", "delete"])
def test_changed_quantity_conflicts_even_if_checked_has_not_changed(test_db, kind):
    db = household(test_db)
    first = db.cart_sync([add()])[0]
    revision = cart_content_revision(db.cart_list()[0])
    db.cart_sync([add("operation-another-add")])
    operation = {"operation_id": "operation-stale-0001", "kind": kind,
                 "item_id": first["item_id"], "expected_checked": False, "expected_revision": revision}
    if kind == "check":
        operation["checked"] = True
    assert db.cart_sync([operation])[0]["reason"] == "item_changed"
    assert db.cart_list()[0]["amount"] == 4000
    assert not db.cart_list()[0]["checked"]


def test_existing_row_can_check_then_uncheck_in_one_offline_batch(test_db):
    db = household(test_db)
    first = db.cart_sync([add()])[0]
    operations = []
    for index, checked in enumerate([True, False]):
        operations.append({"operation_id": f"operation-toggle-{index}", "kind": "check", "item_id": first["item_id"],
                           "expected_checked": not checked, "checked": checked, "expected_revision": first["revision"]})
    assert all(item["status"] == "applied" for item in db.cart_sync(operations))
    assert not db.cart_list()[0]["checked"]


def test_adding_existing_checked_item_reopens_once_and_allows_chained_check(test_db):
    db = household(test_db)
    first = db.cart_sync([add()])[0]
    db.cart_update(first["item_id"], checked=True)
    operation = add("operation-second-add")
    result = db.cart_sync([operation, {"operation_id": "operation-check-again", "kind": "check",
                                     "target_operation_id": operation["operation_id"], "expected_checked": False, "checked": True}])
    assert all(item["status"] == "applied" for item in result)
    db.cart_sync([operation])
    assert db.cart_list()[0]["checked"]
    assert db.cart_list()[0]["amount"] == 4000


def test_receipts_and_item_targets_are_household_scoped(test_db):
    anna, bert = household(test_db, 101), household(test_db, 202)
    anna_id = anna.cart_sync([add()])[0]["item_id"]
    bert_id = bert.cart_sync([add()])[0]["item_id"]
    assert anna_id != bert_id
    result = bert.cart_sync([{"operation_id": "operation-guessed-1", "kind": "delete",
                             "item_id": anna_id, "expected_checked": False}])
    assert result[0]["reason"] == "item_missing"
    assert len(anna.cart_list()) == len(bert.cart_list()) == 1


def test_http_bounds_scope_and_guest(client, test_db, monkeypatch):
    from app.routes import api_shopping
    scoped = household(test_db)
    monkeypatch.setattr(api_shopping, "get_db", lambda: scoped)
    payload = {"household_id": 101, "operations": [add()]}
    assert client.get("/api/cart").json()["household_id"] == 101
    first = client.post("/api/cart/sync", json=payload)
    assert first.status_code == 200, first.text
    assert client.post("/api/cart/sync", json=payload).json() == first.json()
    assert client.post("/api/cart/sync", json={**payload, "household_id": 202}).status_code == 409
    for operations in ([add()] * 101, [add(), add()], [add(name=" " * 3)], [add(name="x" * 201)],
                       [{"kind": "check", "operation_id": "operation-invalid", "checked": True, "expected_checked": False}]):
        assert client.post("/api/cart/sync", json={**payload, "operations": operations}).status_code == 422
    monkeypatch.setattr(api_shopping, "get_db", lambda: HouseholdDatabase(test_db, HouseholdScope(-1, is_guest=True)))
    assert client.post("/api/cart/sync", json=payload).status_code == 403
