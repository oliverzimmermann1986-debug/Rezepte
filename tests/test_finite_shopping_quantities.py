"""Finite API quantities and the schema-266 final database write boundary."""

import json
import math
import sqlite3
import sys
from datetime import date, timedelta

import pytest

from app.accounts import _account
from app.db import CURRENT_SCHEMA_VERSION, Database
from app.tenant_db import HouseholdDatabase
from app.tenancy import HouseholdScope
from tests.conftest import _create_recipe


TABLES = ("shopping_cart", "shopping_recurring")
TRIGGERS = {
    f"{table}_finite_amount_{operation}"
    for table in TABLES
    for operation in ("insert", "update")
}


def _rows(database, table):
    with database.conn() as connection:
        return [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY id")]


def _insert_amount(connection, table, amount, *, account_id=0, suffix="item"):
    if table == "shopping_cart":
        return connection.execute(
            "INSERT INTO shopping_cart "
            "(name, canonical_name, amount, unit, checked, added_at, source_recipe_ids, "
            "category, sort_order, account_id) VALUES (?, ?, ?, 'g', 1, 123, '[41,42]', 'Test', 7, ?)",
            (f"Article {suffix}", f"article-{suffix}", amount, account_id),
        ).lastrowid
    return connection.execute(
        "INSERT INTO shopping_recurring "
        "(name, canonical_name, amount, unit, category, interval_days, next_due_on, active, "
        "last_added_at, created_at, updated_at, account_id) "
        "VALUES (?, ?, ?, 'g', 'Test', 7, '2030-01-01', 0, 120, 121, 122, ?)",
        (f"Article {suffix}", f"article-{suffix}", amount, account_id),
    ).lastrowid


def _restore_schema_265(database):
    """A real v265 database has neither the marker nor the v266 triggers."""
    with database.conn() as connection:
        for name in sorted(TRIGGERS):
            connection.execute(f"DROP TRIGGER {name}")
        connection.execute("DELETE FROM schema_migrations WHERE version=266")


@pytest.mark.parametrize("raw_value", ["NaN", "Infinity", "-Infinity", '"NaN"', '"Infinity"', "1e999"])
@pytest.mark.parametrize(
    "method,path,payload_prefix,field",
    [
        ("POST", "/api/cart/add", '"name":"Invalid",', "amount"),
        ("PATCH", "/api/cart/1", "", "amount"),
        ("POST", "/api/cart/recurring", '"name":"Invalid",', "amount"),
        ("PATCH", "/api/cart/recurring/1", "", "amount"),
        ("POST", "/api/cart/cook/1", "", "multiplier"),
    ],
)
def test_nonfinite_api_values_return_serializable_422_without_writes(
    client, test_db, raw_value, method, path, payload_prefix, field
):
    with test_db.conn() as connection:
        for table in TABLES:
            _insert_amount(connection, table, 2)
    before = {table: _rows(test_db, table) for table in TABLES}

    response = client.request(
        method,
        path,
        content="{" + payload_prefix + json.dumps(field) + ":" + raw_value + "}",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "Bitte gib eine gültige, endliche Menge ein."
    assert {table: _rows(test_db, table) for table in TABLES} == before


@pytest.mark.parametrize("path,unit_field", [("/api/cart/add", "unit"), ("/api/cart/recurring", "default_unit")])
def test_finite_input_that_overflows_unit_conversion_is_rejected(client, test_db, path, unit_field):
    amount = 1e308
    assert math.isfinite(amount)
    response = client.post(path, json={"name": "Overflow", "amount": amount, unit_field: "kg"})

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "Die Menge ist zu groß oder ungültig. Bitte prüfe die Mengenangabe."
    assert all(_rows(test_db, table) == [] for table in TABLES)
    with test_db.conn() as connection:
        assert connection.execute("SELECT COUNT(*) FROM shopping_products").fetchone()[0] == 0


def test_recurring_update_conversion_overflow_keeps_original_rule(client, test_db):
    created = client.post("/api/cart/recurring", json={
        "name": "Milch", "amount": 2, "default_unit": "l", "active": False,
    })
    assert created.status_code == 200, created.text
    before = _rows(test_db, "shopping_recurring")

    changed = client.patch(f"/api/cart/recurring/{created.json()['id']}", json={
        "amount": 1e308, "default_unit": "l",
    })

    assert changed.status_code == 422, changed.text
    assert _rows(test_db, "shopping_recurring") == before


def test_finite_cart_sum_overflow_rolls_back_existing_item_and_catalog(client, test_db):
    first = client.post("/api/cart/add", json={"name": "Reis", "amount": 1e308, "unit": "g"})
    assert first.status_code == 200, first.text
    test_db.cart_update(first.json()["id"], checked=True)
    before = _rows(test_db, "shopping_cart")
    with test_db.conn() as connection:
        products = [tuple(row) for row in connection.execute("SELECT * FROM shopping_products")]

    overflow = client.post("/api/cart/add", json={"name": "Reis", "amount": 1e308, "unit": "g"})

    assert overflow.status_code == 422, overflow.text
    assert _rows(test_db, "shopping_cart") == before
    with test_db.conn() as connection:
        assert [tuple(row) for row in connection.execute("SELECT * FROM shopping_products")] == products


@pytest.mark.parametrize("unit,multiplier", [("g", 100), ("kg", 1)])
def test_recipe_scaling_or_conversion_overflow_never_stores_infinite_amounts(
    client, test_db, tmp_path, unit, multiplier
):
    recipe = _create_recipe(test_db, name="Large recipe", folder_path=str(tmp_path / "recipe"))
    test_db.recipe_set_extraction_result(recipe["id"], "ok", [{
        "name": "Reis", "canonical_name": "reis", "amount": 1e308, "unit": unit,
    }])

    response = client.post(f"/api/cart/cook/{recipe['id']}", json={"multiplier": multiplier})

    assert response.status_code == 422, response.text
    assert _rows(test_db, "shopping_cart") == []
    assert test_db.recipe_ingredients_get(recipe["id"])[0]["amount"] == 1e308


def _shopping_writer(test_db, monkeypatch, household):
    if not household:
        return test_db, 0
    from app.routes import api_shopping

    users = [test_db.user_create(name, "synthetic-test-hash") for name in ("batch-owner", "other-owner")]
    with test_db.conn() as connection:
        accounts = [_account(connection, user_id)["id"] for user_id in users]
    writer = HouseholdDatabase(test_db, HouseholdScope(accounts[0]))
    other = HouseholdDatabase(test_db, HouseholdScope(accounts[1]))
    other.cart_add_or_merge(name="Reis", canonical_name="reis", amount=42, unit="g", source_recipe_id=None)
    monkeypatch.setattr(api_shopping, "get_db", lambda: writer)
    return writer, accounts[0]


def _cart_and_catalog_snapshot(database):
    with database.conn() as connection:
        return {
            "cart": [tuple(row) for row in connection.execute("SELECT * FROM shopping_cart ORDER BY id")],
            "catalog": [tuple(row) for row in connection.execute(
                "SELECT * FROM shopping_products ORDER BY account_id, canonical_name"
            )],
        }


@pytest.mark.parametrize("household", [False, True], ids=["legacy", "household"])
@pytest.mark.parametrize("failure", ["conversion", "scaling", "duplicate_sum", "existing_sum"])
def test_late_recipe_overflow_rolls_back_entire_cart_batch(
    client, test_db, tmp_path, monkeypatch, household, failure
):
    writer, _ = _shopping_writer(test_db, monkeypatch, household)
    recipe = _create_recipe(test_db, name="Batch recipe", folder_path=str(tmp_path / "batch-recipe"))
    ingredients = [
        {"name": "Mehl", "canonical_name": "mehl", "amount": 2, "unit": "g"},
        {"name": "Reis", "canonical_name": "reis", "amount": 1e308,
         "unit": "kg" if failure == "conversion" else "g"},
    ]
    if failure == "duplicate_sum":
        ingredients.append(dict(ingredients[-1]))
    if failure == "existing_sum":
        item_id = writer.cart_add_or_merge(
            name="Reis", canonical_name="reis", amount=1e308, unit="g", source_recipe_id=None,
        )
        writer.cart_update(item_id, checked=True)
    test_db.recipe_set_extraction_result(recipe["id"], "ok", ingredients)
    before = _cart_and_catalog_snapshot(test_db)

    response = client.post(f"/api/cart/cook/{recipe['id']}", json={
        "multiplier": 100 if failure == "scaling" else 1,
    })

    assert response.status_code == 422, response.text
    assert _cart_and_catalog_snapshot(test_db) == before


@pytest.mark.parametrize("household", [False, True], ids=["legacy", "household"])
def test_successful_recipe_batch_preserves_merge_counts_units_exclusions_and_catalog(
    client, test_db, tmp_path, monkeypatch, household
):
    writer, account_id = _shopping_writer(test_db, monkeypatch, household)
    recipe = _create_recipe(test_db, name="Mixed recipe", folder_path=str(tmp_path / "mixed-recipe"))
    test_db.recipe_set_extraction_result(recipe["id"], "ok", [
        {"name": "Reis", "canonical_name": "reis", "amount": 1, "unit": "kg"},
        {"name": "Reis", "canonical_name": "reis", "amount": 500, "unit": "g"},
        {"name": "Reis", "canonical_name": "reis", "amount": 2, "unit": "Stück"},
        {"name": "Salz", "canonical_name": "salz", "amount": 1, "unit": "Prise"},
    ])
    writer.shopping_exclusion_set("salz", True)
    item_id = writer.cart_add_or_merge(
        name="Reis", canonical_name="reis", amount=250, unit="g", source_recipe_id=None,
    )
    writer.cart_update(item_id, checked=True)
    with test_db.conn() as connection:
        usage_before = connection.execute(
            "SELECT usage_count FROM shopping_products WHERE account_id=? AND canonical_name='reis'",
            (account_id,),
        ).fetchone()[0]

    response = client.post(f"/api/cart/cook/{recipe['id']}", json={"multiplier": 1})

    assert response.status_code == 200, response.text
    assert {key: response.json()[key] for key in ("added", "merged", "skipped")} == {
        "added": 1, "merged": 2, "skipped": 1,
    }
    cart = {row["unit"]: row for row in writer.cart_list()}
    assert set(cart) == {"g", "Stück"}
    assert cart["g"]["amount"] == 1750
    assert cart["g"]["id"] == item_id and cart["g"]["checked"] == 0
    assert cart["Stück"]["amount"] == 2
    assert all(json.loads(row["source_recipe_ids"]) == [recipe["id"]] for row in cart.values())
    with test_db.conn() as connection:
        assert connection.execute(
            "SELECT usage_count FROM shopping_products WHERE account_id=? AND canonical_name='reis'",
            (account_id,),
        ).fetchone()[0] == usage_before + 3
        if household:
            assert connection.execute(
                "SELECT amount FROM shopping_cart WHERE account_id<>?", (account_id,),
            ).fetchone()[0] == 42


def test_recurring_sum_overflow_rolls_back_cart_and_due_dates(client, test_db):
    cart = client.post("/api/cart/add", json={"name": "Reis", "amount": 1e308, "unit": "g"})
    assert cart.status_code == 200, cart.text
    test_db.cart_update(cart.json()["id"], checked=True)
    for name, amount in [("Nudeln", 2), ("Reis", 1e308)]:
        created = client.post("/api/cart/recurring", json={
            "name": name, "amount": amount, "default_unit": "g",
            "next_due_on": (date.today() - timedelta(days=1)).isoformat(),
        })
        assert created.status_code == 200, created.text
    before = {table: _rows(test_db, table) for table in TABLES}

    response = client.post("/api/cart/recurring/run", json={})

    assert response.status_code == 422, response.text
    assert {table: _rows(test_db, table) for table in TABLES} == before


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("operation", ["insert", "update"])
@pytest.mark.parametrize("amount", [float("inf"), float("-inf"), "NaN", b"invalid"])
def test_database_triggers_reject_invalid_quantities_and_preserve_rows(test_db, table, operation, amount):
    with test_db.conn() as connection:
        item_id = _insert_amount(connection, table, 3)
    before = _rows(test_db, table)

    with pytest.raises(sqlite3.IntegrityError, match="^shopping_quantity_not_finite$"):
        with test_db.conn() as connection:
            if operation == "insert":
                _insert_amount(connection, table, amount, suffix="new")
            else:
                connection.execute(f"UPDATE {table} SET amount=? WHERE id=?", (amount, item_id))

    assert _rows(test_db, table) == before


@pytest.mark.parametrize("table", TABLES)
def test_database_allows_finite_limits_and_optional_unknown_amounts(test_db, table):
    amounts = [sys.float_info.max, -sys.float_info.max, 0.0, 0.25, None]
    with test_db.conn() as connection:
        for index, amount in enumerate(amounts):
            _insert_amount(connection, table, amount, suffix=str(index))

    assert [row["amount"] for row in _rows(test_db, table)] == amounts


def test_v266_migration_only_clears_invalid_amounts_preserving_households_and_backup(tmp_path):
    path = tmp_path / "legacy-quantities.db"
    database = Database(path)
    users = [database.user_create(name, "synthetic-test-hash") for name in ("alice", "bob")]
    with database.conn() as connection:
        household_ids = [_account(connection, user_id)["id"] for user_id in users]
    _restore_schema_265(database)
    invalid_values = [float("inf"), float("-inf"), "NaN", "Infinity", "invalid", b"invalid"]
    valid_values = [None, 0.0, 0.25, sys.float_info.max, -sys.float_info.max]
    expected = {}
    with database.conn() as connection:
        for table in TABLES:
            invalid_ids = set()
            for household_id in household_ids:
                for index, value in enumerate(invalid_values + valid_values):
                    item_id = _insert_amount(connection, table, value, account_id=household_id,
                                             suffix=f"{household_id}-{index}")
                    if index < len(invalid_values):
                        invalid_ids.add(item_id)
            expected[table] = [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY id")]
            for row in expected[table]:
                if row["id"] in invalid_ids:
                    row["amount"] = None
    before_accounts = _rows(database, "user_accounts")

    upgraded = Database(path)

    assert {table: _rows(upgraded, table) for table in TABLES} == expected
    assert _rows(upgraded, "user_accounts") == before_accounts
    with upgraded.conn() as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == CURRENT_SCHEMA_VERSION
    backups = list((tmp_path / "backups").glob("pre-migration-v265-to-v266-*.db"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 265
        for table in TABLES:
            values = [row[0] for row in backup.execute(f"SELECT amount FROM {table} ORDER BY id")]
            assert values == (invalid_values + valid_values) * len(household_ids)

    for _ in range(2):
        Database(path)
    assert {table: _rows(upgraded, table) for table in TABLES} == expected
    assert len(list((tmp_path / "backups").glob("pre-migration-v265-to-v266-*.db"))) == 1
    with upgraded.conn() as connection:
        trigger_names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        assert TRIGGERS <= trigger_names
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=266").fetchone()[0] == 1


def test_failed_v266_migration_rolls_back_cleanup_and_triggers(tmp_path, monkeypatch):
    path = tmp_path / "migration-rollback.db"
    database = Database(path)
    _restore_schema_265(database)
    with database.conn() as connection:
        for table in TABLES:
            _insert_amount(connection, table, float("inf"))
    real_migrate = Database._migrate

    def fail_after_migration(connection):
        real_migrate(connection)
        raise RuntimeError("synthetic migration interruption")

    with monkeypatch.context() as patch:
        patch.setattr(Database, "_migrate", staticmethod(fail_after_migration))
        with pytest.raises(RuntimeError, match="synthetic migration interruption"):
            Database(path)

    with database.conn() as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 265
        trigger_names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        assert not TRIGGERS & trigger_names
        for table in TABLES:
            assert connection.execute(f"SELECT amount FROM {table}").fetchone()[0] == float("inf")

    Database(path)
    for table in TABLES:
        assert _rows(database, table)[0]["amount"] is None
