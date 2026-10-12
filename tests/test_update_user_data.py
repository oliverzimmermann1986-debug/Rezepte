"""Reject lost user state while rollback is still safe, before public startup."""
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

from app import accounts
from app.db import Database
from app.recipes.cart_logic import cart_for_display
from app.recipes.meal_plan_shopping import sync_week
from app.tenancy import HouseholdScope
from app.tenant_db import HouseholdDatabase
from tests.conftest import _create_recipe
from tools.release_state import capture, verify_user_data


def seeded(tmp_path):
    app, state = tmp_path / 'app', tmp_path / 'snapshot'
    (app / 'data').mkdir(parents=True)
    path = app / 'data/recipes.db'
    (app / 'data/config.yaml').write_text(yaml.safe_dump({'paths': {'db_path': str(path)}}))
    db = Database(path)
    user = db.user_create('original', 'unchanged-hash')
    household = accounts.view(db, user)['id']
    rid = _create_recipe(db, name='Suppe', folder_path='/synthetic/suppe')['id']
    db.recipe_set_extraction_result(rid, 'ok', [{'name': 'Kartoffeln', 'amount': 300, 'unit': 'g'}])
    db.recipe_set_servings(rid, 2)
    scoped = HouseholdDatabase(db, HouseholdScope(household))
    scoped.meal_plan_add(planned_for='2026-10-14', recipe_id=rid, planned_servings=4)
    with scoped.conn() as c:
        c.execute('BEGIN IMMEDIATE')
        sync_week(c, scoped, household, '2026-10-12', enable=True)
        c.execute('UPDATE shopping_cart SET checked=1 WHERE account_id=?', (household,))
        c.execute('INSERT OR REPLACE INTO account_recipe_state(account_id,recipe_id,saved_at,is_favorite,rating,is_saved) VALUES(?,?,1,1,5,1)', (household, rid))
        c.execute("INSERT INTO household_meal_wishes(account_id,week_start,recipe_id,created_by,created_by_key,created_at) VALUES(?,'2026-10-12',?,'original','original',1)", (household, rid))
        c.execute("INSERT INTO household_cookbooks(account_id,name,name_key,created_at) VALUES(?,'Lieblinge','lieblinge',1)", (household,))
    return app, state, db, scoped


def test_real_273_upgrade_keeps_plan_checked_cart_provenance_links_and_preferences(tmp_path):
    app, state, db, scoped = seeded(tmp_path)
    original_cart = cart_for_display(scoped)
    assert original_cart[0]['checked'] and original_cart[0]['source_contributions'][0]['amount'] == 600
    with db.conn() as c:
        c.execute('ALTER TABLE user_accounts DROP COLUMN default_servings')
        c.execute('DELETE FROM schema_migrations WHERE version=274')
    capture(app, state, unit_root=tmp_path / 'units')
    Database(db.path)
    report = verify_user_data(app, state)
    assert report['user_data_preserved'] and report['before_public_start']
    for table in ('meal_plan_entries', 'shopping_cart', 'shopping_sync_operations', 'account_recipe_state', 'household_meal_wishes', 'household_cookbooks'):
        assert report['tables'][table] == 1
    assert cart_for_display(scoped) == original_cart
    with db.conn() as c:
        assert c.execute('SELECT default_servings FROM user_accounts WHERE id=?', (scoped.account_id,)).fetchone()[0] == 2
    Database(db.path)
    assert verify_user_data(app, state) == report


@pytest.mark.parametrize('table,column,value', [
    ('shopping_cart', 'checked', 0),
    ('meal_plan_entries', 'planned_servings', 1),
    ('user_accounts', 'default_servings', 9),
    ('account_recipe_state', 'rating', 0),
    ('household_cookbooks', 'name', 'lost'),
])
def test_changed_user_values_are_rejected_without_exposing_rows(tmp_path, table, column, value):
    app, state, db, _ = seeded(tmp_path)
    capture(app, state, unit_root=tmp_path / 'units')
    with db.conn() as c:
        c.execute(f'UPDATE {table} SET {column}=?', (value,))
    current = db.path.read_bytes()
    with pytest.raises(ValueError, match=table) as failure:
        verify_user_data(app, state)
    assert 'unchanged-hash' not in str(failure.value)
    assert db.path.read_bytes() == current


def test_missing_rows_and_columns_fail_but_additive_schema_is_accepted(tmp_path):
    app, state, db, _ = seeded(tmp_path)
    capture(app, state, unit_root=tmp_path / 'units')
    with db.conn() as c:
        c.execute('ALTER TABLE shopping_cart ADD COLUMN future_field TEXT')
    assert verify_user_data(app, state)['user_data_preserved']
    with db.conn() as c:
        c.execute('DELETE FROM meal_plan_entries')
    with pytest.raises(ValueError, match='meal_plan_entries'):
        verify_user_data(app, state)
    with db.conn() as c:
        c.execute('ALTER TABLE shopping_cart DROP COLUMN future_field')
        c.execute('ALTER TABLE shopping_cart DROP COLUMN source_contributions')
    with pytest.raises(ValueError, match='Felder fehlen: shopping_cart'):
        verify_user_data(app, state)


def test_updater_checks_migration_before_first_start_and_never_rolls_back_late_writes():
    script = Path('proxmox/update-local.sh').read_text(encoding='utf-8')
    gate = script.index('release_state.py" verify-user-data')
    public = script.index('PUBLIC_WRITES_POSSIBLE=1')
    start = script.index('systemctl restart scrapper-web.service\n', public)
    assert script.index("-c 'from app.db import get_db; get_db()'") < gate < public < start
    bash = shutil.which('bash') or 'C:/Program Files/Git/bin/bash.exe'
    if not Path(bash).is_file():
        pytest.skip('Bash required for late failure simulation')
    function = script.split('restore_on_error() {', 1)[1].split('\ntrap restore_on_error ERR', 1)[0]
    harness = '\n'.join([
        'set -uo pipefail; PUBLIC_WRITES_POSSIBLE=1; STATE_DIR=synthetic-snapshot',
        'stop_installed_units() { printf "STOP %s\\n" "$*"; }',
        'rsync() { echo WOULD_LOSE_NEW_WRITES; }; tar() { echo WOULD_RESTORE_OLD_CODE; }',
        'restore_on_error() {' + function,
        'false; restore_on_error',
    ])
    result = subprocess.run([bash, '-c', harness], capture_output=True, text=True)
    assert result.returncode == 1
    assert 'Aktuelle Daten bleiben erhalten' in result.stderr
    assert 'WOULD_' not in result.stdout
