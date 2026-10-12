"""Durable defaults shared by members; explicit and existing portions win."""
import sqlite3

import pytest

from app import accounts, auth
from app.db import Database
from tests.test_accounts import account_client  # noqa: F401
from tests.test_discovery import _recipe


def test_member_changes_default_for_both_sessions_without_changing_other_household(account_client, test_db):
    client, owner = account_client
    house = client.get('/api/account').json()
    assert house['default_servings'] == 2
    invitation = client.post('/api/account/invitations').json()
    partner = accounts.register(test_db, 'servings-partner', 'synthetic-hash', invitation_token=invitation['token'])
    stranger = test_db.user_create('servings-other', 'synthetic-hash')
    accounts.view(test_db, stranger)
    client.headers['Authorization'] = 'Bearer ' + auth.create_session('servings-partner')
    response = client.patch('/api/account/preferences', json={'default_servings': 4})
    assert response.status_code == 200 and response.json() == {'ok': True, 'household_id': house['id'], 'default_servings': 4}
    assert accounts.view(test_db, owner)['default_servings'] == accounts.view(test_db, partner)['default_servings'] == 4
    assert accounts.view(test_db, stranger)['default_servings'] == 2
    client.headers['Authorization'] = 'Bearer ' + auth.create_session('owner')
    for endpoint in ('/api/auth/session', '/api/session'):
        payload = client.get(endpoint).json()
        assert payload['household_id'] == house['id'] and payload['default_servings'] == 4
    restarted = Database(test_db.path)
    assert accounts.view(restarted, owner)['default_servings'] == 4


@pytest.mark.parametrize('value', [0, 25, -1, 1.5, True, '4', None])
def test_preferences_reject_invalid_values_without_mutation(account_client, test_db, value):
    client, owner = account_client
    assert client.patch('/api/account/preferences', json={'default_servings': value}).status_code == 422
    assert accounts.view(test_db, owner)['default_servings'] == 2


def test_preferences_cannot_select_a_foreign_household_and_readonly_cannot_write(account_client, test_db):
    client, owner = account_client
    assert client.patch('/api/account/preferences', json={'default_servings': 4, 'household_id': 999}).status_code == 422
    for token in (auth.create_guest_session(), None):
        if token: client.headers['Authorization'] = 'Bearer ' + token
        else: client.headers.pop('Authorization', None)
        assert client.patch('/api/account/preferences', json={'default_servings': 4}).status_code in (401, 403)
    test_db.user_create('servings-readonly', 'synthetic-hash', role='guest')
    client.headers['Authorization'] = 'Bearer ' + auth.create_session('servings-readonly')
    assert client.patch('/api/account/preferences', json={'default_servings': 4}).status_code == 403
    assert client.get('/api/auth/session').json().get('default_servings') is None
    assert accounts.view(test_db, owner)['default_servings'] == 2


def test_new_plan_uses_default_but_explicit_portions_and_linked_cart_remain_unchanged(account_client, test_db):
    client, _ = account_client
    rid = _recipe(test_db, 'ServingPlan')
    assert client.patch('/api/account/preferences', json={'default_servings': 4}).status_code == 200
    response = client.post('/api/meal-plan/items', json={'recipe_id': rid, 'planned_for': '2026-10-12'})
    assert response.status_code == 200 and response.json()['item']['planned_servings'] == 4
    assert client.get('/api/meal-plan?week_start=2026-10-12').json()['default_servings'] == 4
    assert client.post('/api/meal-plan/items', json={'recipe_id': rid, 'planned_for': '2026-10-13', 'planned_servings': 3}).json()['item']['planned_servings'] == 3
    assert client.post('/api/meal-plan/cart', json={'week_start': '2026-10-12'}).status_code == 200
    with test_db.conn() as c:
        before = [tuple(row) for row in c.execute('SELECT * FROM shopping_cart ORDER BY id')]
    assert client.patch('/api/account/preferences', json={'default_servings': 6}).status_code == 200
    retry = client.post('/api/meal-plan/items', json={'recipe_id': rid, 'planned_for': '2026-10-12'})
    assert retry.json()['item']['planned_servings'] == 4
    week = client.get('/api/meal-plan?week_start=2026-10-12').json()
    assert [entry['planned_servings'] for day in week['days'] for entry in day['items']] == [4, 3]
    with test_db.conn() as c:
        assert [tuple(row) for row in c.execute('SELECT * FROM shopping_cart ORDER BY id')] == before


def test_wish_uses_household_default_when_portions_omitted(account_client, test_db):
    client, _ = account_client
    assert client.patch('/api/account/preferences', json={'default_servings': 5}).status_code == 200
    rid = _recipe(test_db, 'ServingWish')
    response = client.post('/api/meal-wishes', json={'recipe_id': rid, 'week_start': '2026-10-12'})
    assert response.status_code == 200, response.text
    wish = response.json()['item']['id']
    response = client.post(f'/api/meal-wishes/{wish}/plan', json={'planned_for': '2026-10-14'})
    assert response.status_code == 200, response.text
    week = client.get('/api/meal-plan?week_start=2026-10-12').json()
    assert week['days'][2]['items'][0]['planned_servings'] == 5


def test_join_adopts_target_default_and_owner_transfer_retains_it(account_client, test_db):
    client, owner = account_client
    assert client.patch('/api/account/preferences', json={'default_servings': 4}).status_code == 200
    joining = test_db.user_create('servings-joining', 'synthetic-hash')
    accounts.save_preferences(test_db, joining, 6)
    target = accounts.view(test_db, owner)['id']
    token = accounts.invite(test_db, owner)['token']
    accounts.accept(test_db, joining, token)
    assert accounts.preferences_for_user(test_db, joining) == {'household_id': target, 'default_servings': 4}
    test_db.user_delete(owner)
    assert accounts.view(test_db, joining)['default_servings'] == 4


def test_schema_273_upgrade_defaults_existing_households_and_is_idempotent(tmp_path):
    path = tmp_path / 'servings-upgrade.db'
    previous = Database(path)
    owner = previous.user_create('old-owner', 'unchanged-hash')
    old = accounts.view(previous, owner)
    with previous.conn() as c:
        c.execute('ALTER TABLE user_accounts DROP COLUMN default_servings')
        c.execute('DELETE FROM schema_migrations WHERE version=274')
    current = Database(path)
    assert accounts.view(current, owner)['id'] == old['id']
    assert accounts.view(current, owner)['default_servings'] == 2
    assert current.user_get_by_name('old-owner')['password_hash'] == 'unchanged-hash'
    backups = set((tmp_path / 'backups').glob('pre-migration-v273-to-v274-*.db'))
    assert len(backups) == 1
    with current.conn() as c:
        assert c.execute('PRAGMA foreign_key_check').fetchall() == []
        with pytest.raises(sqlite3.IntegrityError):
            c.execute('UPDATE user_accounts SET default_servings=25')
    accounts.save_preferences(current, owner, 7)
    assert accounts.view(Database(path), owner)['default_servings'] == 7
    assert set((tmp_path / 'backups').glob('pre-migration-v273-to-v274-*.db')) == backups
