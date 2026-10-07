"""The native quantity editor updates an existing row in stored base units."""

import pytest


@pytest.mark.parametrize(
    "name,initial,unit,base_amount,display_amount,display_unit,base_unit",
    [
        ("Toilettenpapier", 4, "Stück", 2, 2, "Stück", "Stück"),
        ("Reis", 1.5, "kg", 2000, 2, "kg", "g"),
        ("Milch", 1.5, "l", 2000, 2, "l", "ml"),
        ("Safran", 300, "mg", 0.2, 200, "mg", "g"),
    ],
)
@pytest.mark.parametrize("checked", [False, True])
def test_amount_only_patch_persists_without_changing_other_item_fields(
    client, test_db, name, initial, unit, base_amount, display_amount,
    display_unit, base_unit, checked,
):
    created = client.post(
        "/api/cart/add", json={"name": name, "amount": initial, "unit": unit},
    )
    assert created.status_code == 200, created.text
    item_id = created.json()["id"]
    checked_response = client.patch(f"/api/cart/{item_id}", json={"checked": checked})
    assert checked_response.status_code == 200, checked_response.text
    with test_db.conn() as connection:
        before = dict(connection.execute(
            "SELECT * FROM shopping_cart WHERE id=?", (item_id,),
        ).fetchone())

    changed = client.patch(f"/api/cart/{item_id}", json={"amount": base_amount})
    assert changed.status_code == 200, changed.text

    with test_db.conn() as connection:
        after = dict(connection.execute(
            "SELECT * FROM shopping_cart WHERE id=?", (item_id,),
        ).fetchone())
    assert after == {**before, "amount": base_amount}
    refreshed = client.get("/api/cart")
    assert refreshed.status_code == 200, refreshed.text
    item = next(item for item in refreshed.json()["items"] if item["id"] == item_id)
    assert item["amount"] == pytest.approx(display_amount)
    assert item["unit"] == display_unit
    assert item["amount_base"] == pytest.approx(base_amount)
    assert item["unit_base"] == base_unit
    assert item["name"] == name
    assert item["checked"] is checked
