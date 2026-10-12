from copy import deepcopy
import json

import pytest

from app.recipes.cart_logic import add_recipe_to_cart, aggregate_recipes_for_cart, cart_for_display, prepare_for_cart
from app.recipes.shopping_optimizer import build_optimized_cart
from app.recipes.meal_plan_shopping import sync_week, journal_key
from app.tenant_db import HouseholdDatabase
from app.tenancy import HouseholdScope
from tests.conftest import _create_recipe


def rows(checked=False):
    return [
        {'id': 1, 'name': 'Knoblauch', 'amount': 2, 'unit': 'Zehe', 'checked': checked,
         'source_recipe_ids': [101], 'source_contributions': [{'recipe_id': 101, 'amount': 2, 'unit': 'Zehe', 'kind': 'recipe'}]},
        {'id': 2, 'name': 'Knoblauchzehe', 'amount': 0.5, 'unit': 'Stück', 'checked': checked,
         'source_recipe_ids': [102], 'source_contributions': [{'recipe_id': 102, 'amount': 0.5, 'unit': 'Stück', 'kind': 'recipe'}]},
    ]


@pytest.mark.parametrize('rename', [False, True])
def test_screenshot_case_merges_without_ai_quantity_authority_or_source_loss(rename):
    items = rows()
    original = deepcopy(items)
    suggestions = [{'id': item['id'], 'name': 'Knoblauch' if rename else item['name'],
                    'amount': 999, 'unit': 'kg', 'category': 'Obst & Gemüse'} for item in items]
    result = build_optimized_cart(items, suggestions)
    assert len(result['items']) == 1
    merged = result['items'][0]
    assert (merged['name'], merged['amount'], merged['unit']) == ('Knoblauch', 2.5, 'Zehe')
    assert merged['source_recipe_ids'] == [101, 102]
    assert merged['source_item_ids'] == [1, 2]
    assert [(entry['recipe_id'], entry['amount'], entry['unit']) for entry in merged['source_contributions']] == [(101, 2, 'Zehe'), (102, 0.5, 'Zehe')]
    assert items == original


@pytest.mark.parametrize('name,unit', [
    ('Knoblauch', 'Stück'), ('Knoblauchknolle', 'Stück'),
    ('Knoblauchzehe', 'g'), ('Knoblauchpulver', 'g'), ('Knoblauchzehe', None),
])
def test_other_quantities_are_not_guessed_from_an_ai_name(name, unit):
    prepared = prepare_for_cart('Knoblauch', 1, unit, quantity_name=name)
    assert prepared['unit'] == unit and prepared['amount'] == 1


def test_checked_and_unknown_portions_remain_separate():
    items = rows()
    items[1]['checked'] = True
    assert len(build_optimized_cart(items, [])['items']) == 2
    items[1]['checked'] = False
    items[1]['amount'] = None
    items[1]['source_contributions'][0]['amount'] = None
    result = build_optimized_cart(items, [])
    assert len(result['items']) == 2
    assert result['items'][1]['amount'] is None


def test_recipe_add_week_preview_and_later_plan_changes_use_one_clove_unit(test_db):
    recipe_ids = []
    for name, amount, unit in [('Knoblauch', 2, 'Zehe'), ('Knoblauchzehe', 0.5, 'Stück')]:
        rid = _create_recipe(test_db, name=name, folder_path='/synthetic/' + name)['id']
        test_db.recipe_set_servings(rid, 2)
        test_db.recipe_set_extraction_result(rid, 'ok', [{'name': name, 'amount': amount, 'unit': unit}])
        recipe_ids.append(rid)
    db = HouseholdDatabase(test_db, HouseholdScope(0))
    preview = aggregate_recipes_for_cart(db, [{'recipe_id': rid, 'multiplier': 1} for rid in recipe_ids])
    assert len(preview) == 1 and preview[0]['amount'] == 2.5 and preview[0]['unit'] == 'Zehe'
    for rid in recipe_ids:
        add_recipe_to_cart(db, rid)
    assert len(db.cart_list()) == 1 and cart_for_display(db)[0]['amount'] == 2.5
    db.cart_clear()
    entries = [db.meal_plan_add(planned_for='2026-10-14', recipe_id=rid, planned_servings=2) for rid in recipe_ids]
    with db.conn() as c:
        c.execute('BEGIN IMMEDIATE')
        sync_week(c, db, 0, '2026-10-12', enable=True)
    assert cart_for_display(db)[0]['amount'] == 2.5
    # Reproduce an existing pre-fix linked week, rather than only fresh adds.
    legacy = rows(checked=True)
    for item, recipe_id in zip(legacy, recipe_ids):
        item['canonical_name'] = 'knoblauch'
        item['source_recipe_ids'] = [recipe_id]
        part = item['source_contributions'][0]
        part.update(recipe_id=recipe_id, kind='meal_plan', plan_week='2026-10-12', known_amount=part['amount'])
    db.cart_replace(legacy)
    with db.conn() as c:
        c.execute('UPDATE shopping_sync_operations SET response_json=? WHERE account_id=0 AND operation_id=?',
                  (json.dumps(legacy), journal_key('2026-10-12')))
    optimized = build_optimized_cart(db.cart_list(), [])['items']
    assert optimized[0]['amount'] == 2.5 and optimized[0]['checked']
    assert {part['recipe_id']: part['known_amount'] for part in optimized[0]['source_contributions']} == dict(zip(recipe_ids, [2, 0.5]))
    db.cart_replace(optimized)
    assert cart_for_display(db)[0]['checked']
    db.meal_plan_update(entries[1]['id'], planned_servings=4)
    assert len(db.cart_list()) == 1 and cart_for_display(db)[0]['amount'] == 3
    assert all(part['unit'] == 'Zehe' for part in cart_for_display(db)[0]['source_contributions'])
    # Increasing the required amount legitimately reopens the item for buying.
    assert not cart_for_display(db)[0]['checked']
