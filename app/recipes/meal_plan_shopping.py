"""Atomic, household-scoped reconciliation of explicitly linked weekly shopping.

The reserved journal key contains colons, which public shopping operation IDs
cannot contain. It stores a durable baseline, not a replayable client operation.
Only contributions tagged with this week are changed; manual edits/deletions
detach that ingredient and remain respected by subsequent plan changes.
"""
from __future__ import annotations

from datetime import date, timedelta
import json
import math
import time

from .cart_logic import aggregate_recipes_for_cart
from .shopping_history import combine_contributions, contributions


def week_start(day: str) -> str:
    value = date.fromisoformat(day)
    return (value - timedelta(days=value.weekday())).isoformat()


def journal_key(week: str) -> str:
    return 'meal-plan-week:' + week_start(week)


def linked(c, account_id: int, week: str) -> bool:
    return c.execute('SELECT 1 FROM shopping_sync_operations WHERE account_id=? AND operation_id=?',
                     (account_id, journal_key(week))).fetchone() is not None


def _desired(c, account_id, week):
    end = (date.fromisoformat(week) + timedelta(days=6)).isoformat()
    entries = c.execute('SELECT mp.recipe_id,mp.planned_servings,r.servings FROM meal_plan_entries mp '
                        'JOIN recipes r ON r.id=mp.recipe_id WHERE mp.account_id=? '
                        'AND mp.planned_for BETWEEN ? AND ? AND r.deleted_at IS NULL '
                        'AND (r.owner_account_id IS NULL OR r.is_global=1 OR r.owner_account_id=?) '
                        'ORDER BY mp.planned_for,mp.sort_order,mp.id', (account_id, week, end, account_id)).fetchall()

    class Snapshot:
        def shopping_excluded_canonicals(self):
            return {row[0] for row in c.execute('SELECT canonical_name FROM shopping_exclusions WHERE account_id=?', (account_id,))}

        def recipe_ingredients_get(self, recipe_id):
            return [dict(row) for row in c.execute('SELECT * FROM recipe_ingredients WHERE recipe_id=? ORDER BY sort_order,id', (recipe_id,))]

    selections = []
    for row in entries:
        base = float(row['servings'] or 0)
        selections.append({'recipe_id': row['recipe_id'], 'multiplier': row['planned_servings'] / base if base > 0 else 1})
    result = aggregate_recipes_for_cart(Snapshot(), selections)
    for item in result:
        if item['amount'] is not None and (not math.isfinite(item['amount']) or item['amount'] < 0):
            raise ValueError('Die geplante Einkaufsmenge ist ungültig')
        # Keep the numerical subtotal with the owned cart snapshot as well as
        # the journal. Undo can restore an older snapshot after the journal
        # advanced; reconciliation must subtract what is actually present.
        remainder = max(0.0, float(item['amount'] or 0) - sum(float(entry['amount'] or 0) for entry in item['source_contributions']))
        history = []
        for entry in item['source_contributions']:
            known = entry['amount'] if entry['amount'] is not None else remainder
            if entry['amount'] is None:
                remainder = 0
            history.append({**entry, 'kind': 'meal_plan', 'plan_week': week, 'known_amount': known})
        item['source_contributions'] = history
    return result


def sync_week(c, db, account_id: int, week: str, *, enable=False):
    if not hasattr(db, '_cart_merge'):
        from ..tenant_db import HouseholdDatabase
        from ..tenancy import HouseholdScope
        db = HouseholdDatabase(db, HouseholdScope(account_id))
    week = week_start(week)
    key = journal_key(week)
    receipt = c.execute('SELECT response_json FROM shopping_sync_operations WHERE account_id=? AND operation_id=?', (account_id, key)).fetchone()
    if not receipt and not enable:
        return {'added': 0, 'merged': 0}
    old = json.loads(receipt['response_json']) if receipt else []
    desired = _desired(c, account_id, week)
    previous = {(item['canonical_name'], item['unit']): item for item in old}
    current = {(item['canonical_name'], item['unit']): item for item in desired}
    counters = {'added': 0, 'merged': 0}
    for item_key in previous.keys() | current.keys():
        before, after = previous.get(item_key), current.get(item_key)
        if before == after:
            continue
        row = c.execute('SELECT * FROM shopping_cart WHERE account_id=? AND canonical_name=? AND unit IS ?',
                        (account_id, *item_key)).fetchone()
        history = contributions(dict(row)) if row else []
        owned = [entry for entry in history if entry.get('kind') == 'meal_plan' and entry.get('plan_week') == week]
        if before is not None and not owned:
            # This ingredient was deliberately deleted or freely edited.
            continue
        if not owned:
            if after:
                _, merged = db._cart_merge(c, after, reopen=True)
                counters['merged' if merged else 'added'] += 1
            continue
        remaining = [entry for entry in history if entry not in owned]
        incoming = after['source_contributions'] if after else []
        history = combine_contributions(remaining, incoming)
        if not history:
            c.execute('DELETE FROM shopping_cart WHERE id=? AND account_id=?', (row['id'], account_id))
            continue
        # A recipe may contain both a known and an unknown quantity of the
        # same ingredient. Its combined contribution is then unknown, while
        # the durable aggregate still records the known numerical subtotal.
        old_amount = (sum(float(entry['known_amount']) for entry in owned) if all(entry.get('known_amount') is not None for entry in owned)
                      else float(before['amount'] or 0) if before else sum(float(entry['amount']) for entry in owned if entry.get('amount') is not None))
        new_amount = float(after['amount'] or 0) if after else 0
        amount = max(0.0, float(row['amount'] or 0) - old_amount + new_amount)
        if not math.isfinite(amount):
            raise ValueError('Die geplante Einkaufsmenge ist zu groß')
        if not any(entry.get('amount') is not None for entry in history) and (row['amount'] is None or amount == 0):
            amount = None
        increased = new_amount > old_amount and not math.isclose(new_amount, old_amount, rel_tol=1e-9)
        sources = list(dict.fromkeys(entry['recipe_id'] for entry in history if entry.get('recipe_id')))
        c.execute('UPDATE shopping_cart SET amount=?,source_recipe_ids=?,source_contributions=?,checked=? WHERE id=? AND account_id=?',
                  (amount, json.dumps(sources), json.dumps(history), 0 if increased else row['checked'], row['id'], account_id))
        counters['merged'] += 1
    c.execute('INSERT INTO shopping_sync_operations(account_id,operation_id,request_json,response_json,created_at) '
              'VALUES(?,?,?,?,?) ON CONFLICT(account_id,operation_id) DO UPDATE SET response_json=excluded.response_json,created_at=excluded.created_at',
              (account_id, key, json.dumps({'kind': 'meal_plan_week', 'week_start': week}), json.dumps(desired, allow_nan=False), time.time()))
    return counters


def sync_dates(c, db, account_id, *days):
    for week in {week_start(day) for day in days if day}:
        sync_week(c, db, account_id, week)


def merge_links(c, db, source, target):
    """Transfer durable links together with plan/cart during invitation join."""
    from ..tenant_db import HouseholdDatabase
    from ..tenancy import HouseholdScope
    rows = c.execute("SELECT * FROM shopping_sync_operations WHERE account_id=? AND operation_id LIKE 'meal-plan-week:%'", (source,)).fetchall()
    for row in rows:
        other = c.execute('SELECT response_json FROM shopping_sync_operations WHERE account_id=? AND operation_id=?', (target, row['operation_id'])).fetchone()
        baseline = {(item['canonical_name'], item['unit']): item for item in json.loads(other['response_json'])} if other else {}
        for item in json.loads(row['response_json']):
            key = (item['canonical_name'], item['unit'])
            if key in baseline:
                current = baseline[key]
                values = [value for value in (current['amount'], item['amount']) if value is not None]
                current['amount'] = sum(values) if values else None
                current['source_recipe_ids'] = list(dict.fromkeys(current['source_recipe_ids'] + item['source_recipe_ids']))
                current['source_contributions'] = combine_contributions(current['source_contributions'], item['source_contributions'])
            else:
                baseline[key] = item
        c.execute('INSERT INTO shopping_sync_operations VALUES(?,?,?,?,?) ON CONFLICT(account_id,operation_id) DO UPDATE SET response_json=excluded.response_json,created_at=excluded.created_at',
                  (target, row['operation_id'], row['request_json'], json.dumps(list(baseline.values()), allow_nan=False), time.time()))
    c.execute("DELETE FROM shopping_sync_operations WHERE account_id=? AND operation_id LIKE 'meal-plan-week:%'", (source,))
    writer = HouseholdDatabase(db, HouseholdScope(target))
    for row in c.execute("SELECT operation_id FROM shopping_sync_operations WHERE account_id=? AND operation_id LIKE 'meal-plan-week:%'", (target,)).fetchall():
        sync_week(c, writer, target, row['operation_id'].split(':', 1)[1])
