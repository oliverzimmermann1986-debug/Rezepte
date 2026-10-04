"""Regressionen zu Lauf 6: echte SQLite-Sperren, Upgrade und Requestgrenzen."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import importlib.util
from pathlib import Path
import sqlite3
import shutil
import subprocess

import pytest
from starlette.requests import Request

from app import accounts, security
from app.db import CURRENT_SCHEMA_VERSION, Database
from app.tenant_db import HouseholdDatabase
from app.tenancy import HouseholdScope
from tests.conftest import _create_recipe


def test_sqlite_write_contention_returns_retryable_503(client, test_db, monkeypatch):
    original = test_db.conn
    with original() as writer:
        writer.execute('BEGIN IMMEDIATE')

        @contextmanager
        def brief_wait():
            with original() as connection:
                connection.execute('PRAGMA busy_timeout=100')
                yield connection

        monkeypatch.setattr(test_db, 'conn', brief_wait)
        response = client.post('/api/cart/add', json={'name': 'Milch', 'amount': 1, 'unit': 'l'})
    assert response.status_code == 503
    assert response.headers['retry-after'] == '1'
    assert 'no-store' in response.headers['cache-control']
    assert 'database is locked' not in response.text
    assert test_db.cart_list() == []


def test_login_lock_is_shared_between_html_and_native_but_not_all_nat_users(client, test_db, monkeypatch):
    from app import main
    from app.routes import api_auth
    actor = security.LoginRateLimiter()
    ip_limit = security.LoginRateLimiter(max_fails=100)
    for module in (main, api_auth):
        monkeypatch.setattr(module, 'login_limiter', actor)
        monkeypatch.setattr(module, 'login_ip_limiter', ip_limit)
        monkeypatch.setattr(module, 'client_ip', lambda _request: '192.0.2.100')
        monkeypatch.setattr(module, 'check_credentials', lambda name, pw: pw == 'valid-test-password')
        monkeypatch.setattr(module, 'create_session', lambda name: 'test-token-' + name)
        monkeypatch.setattr(module, 'auth_disabled', lambda: False)
    test_db.user_create('nat-other', 'unused')
    for index in range(5):
        if index % 2:
            response = client.post('/login', data={'username': 'Locked-User', 'password': 'invalid'},
                                   headers={'Origin': 'http://testserver'})
        else:
            response = client.post('/api/auth/login', json={'username': 'locked-user', 'password': 'invalid'})
        assert response.status_code == 401
    blocked = client.post('/api/auth/login', json={'username': 'LOCKED-USER', 'password': 'valid-test-password'})
    assert blocked.status_code == 429 and int(blocked.headers['retry-after']) > 0
    assert client.post('/api/auth/login', json={'username': 'nat-other', 'password': 'valid-test-password'}).status_code == 200
    assert client.post('/login', data={'username': 'locked-user', 'password': 'valid-test-password'},
                       headers={'Origin': 'http://testserver'}).status_code == 429


def _request_ip(peer, headers):
    return Request({'type': 'http', 'method': 'POST', 'path': '/login', 'scheme': 'http',
                    'headers': [(key.lower().encode(), value.encode()) for key, value in headers.items()],
                    'client': (peer, 12345), 'server': ('testserver', 80), 'query_string': b''})


@pytest.mark.parametrize('spoof', [{'CF-Connecting-IP': '198.51.100.20'},
                                {'X-Forwarded-For': '198.51.100.21'},
                                {'CF-Connecting-IP': '198.51.100.22', 'X-Forwarded-For': '198.51.100.23'}])
def test_direct_client_cannot_spoof_rate_limit_identity(spoof):
    assert security.client_ip(_request_ip('192.0.2.30', spoof)) == '192.0.2.30'


def test_trusted_proxy_uses_appended_client_not_spoofed_prefix():
    headers = {'X-Forwarded-For': '198.51.100.12, 192.0.2.20, 127.0.0.1',
               'CF-Connecting-IP': '198.51.100.99'}
    assert security.client_ip(_request_ip('127.0.0.1', headers)) == '192.0.2.20'
    assert security.client_ip(_request_ip('127.0.0.1', {'X-Forwarded-For': 'garbage'})) == '127.0.0.1'
    assert security.client_ip(_request_ip('127.0.0.1', {'CF-Connecting-IP': '198.51.100.20'})) == '198.51.100.20'


def test_logout_without_cookie_still_requires_origin(client):
    response = client.post('/logout', headers={'Origin': 'https://foreign.example'}, follow_redirects=False)
    assert response.status_code == 403 and 'set-cookie' not in response.headers
    assert client.post('/logout', headers={'Origin': 'null'}, follow_redirects=False).status_code == 403
    assert client.post('/logout', headers={'Origin': 'http://testserver'}, follow_redirects=False).status_code == 303


def test_lock_and_cancel_paths_follow_isolated_database(monkeypatch, tmp_path):
    from app.jobs import locks
    from app.config_store import ConfigStore
    cfg = ConfigStore(tmp_path / 'config.yaml')
    cfg.set('paths', 'db_path', str(tmp_path / 'database' / 'recipes.db'))
    monkeypatch.setattr('app.config_store.get_config', lambda: cfg)
    monkeypatch.setattr(locks, 'LOCK_DIR', None)
    assert locks.lock_dir() == tmp_path / 'database' / 'locks'
    with locks.file_lock_or_none('test-scraper') as handle:
        assert handle is not None and Path(handle.name).is_relative_to(tmp_path)
        assert locks.is_locked('test-scraper')
    locks.request_cancel('test-scraper')
    assert locks.cancel_requested('test-scraper')
    locks.clear_cancel('test-scraper')
    assert not locks.cancel_requested('test-scraper')


def test_transient_retry_count_does_not_exhaust_crash_recovery(test_db):
    task_id = test_db.background_task_enqueue('share_ingest', {'url': 'https://example.invalid/test'})
    for _index in range(7):
        assert test_db.background_task_claim_next()['id'] == task_id
        test_db.background_task_retry(task_id, delay_seconds=0, error='temporary')
    test_db.background_task_claim_next()
    assert test_db.background_tasks_recover() == 1
    task = test_db.background_task_get(task_id)
    assert task['status'] == 'queued' and task['attempts'] == 8 and task['recovery_attempts'] == 1
    for _index in range(2):
        test_db.background_task_claim_next()
        assert test_db.background_tasks_recover() == 1
    test_db.background_task_claim_next()
    assert test_db.background_tasks_recover() == 0
    assert test_db.background_task_get(task_id)['status'] == 'error'


def test_recipe_detail_uses_one_snapshot_and_rechecks_next_request(test_db, tmp_path, monkeypatch):
    from app.routes import api_recipes
    owner = test_db.user_create('snapshot-owner', 'unused')
    foreign = test_db.user_create('snapshot-other', 'unused')
    account = accounts.view(test_db, owner)['id']
    other_account = accounts.view(test_db, foreign)['id']
    recipe = _create_recipe(test_db, name='Snapshot', folder_path=str(tmp_path / 'recipe'))
    with test_db.conn() as connection:
        connection.execute('UPDATE recipes SET owner_account_id=? WHERE id=?', (account, recipe['id']))
    scoped = HouseholdDatabase(test_db, HouseholdScope(account, user_id=owner))
    monkeypatch.setattr(api_recipes, 'get_db', lambda: scoped)
    original = sqlite3.connect
    opened = []
    monkeypatch.setattr(sqlite3, 'connect', lambda *args, **kwargs: (opened.append(args[0]) or original(*args, **kwargs)))
    result = api_recipes.get_recipe(recipe['id'])
    assert result['name'] == 'Snapshot' and len(opened) == 1
    with test_db.conn() as connection:
        connection.execute('UPDATE recipes SET owner_account_id=? WHERE id=?', (other_account, recipe['id']))
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as missing:
        api_recipes.get_recipe(recipe['id'])
    assert missing.value.status_code == 404
    with test_db.read_snapshot(), pytest.raises(sqlite3.OperationalError, match='readonly'):
        test_db.recipe_set_servings(recipe['id'], 3)


def test_parallel_detail_snapshots_do_not_cross_households(test_db, tmp_path):
    from app.routes.api_recipes import _recipe_detail
    selections = []
    for index in range(2):
        uid = test_db.user_create(f'parallel-reader-{index}', 'unused')
        aid = accounts.view(test_db, uid)['id']
        row = _create_recipe(test_db, name=f'Privat {index}', folder_path=str(tmp_path / f'recipe-{index}'))
        with test_db.conn() as c:
            c.execute('UPDATE recipes SET owner_account_id=? WHERE id=?', (aid, row['id']))
        selections.append((HouseholdDatabase(test_db, HouseholdScope(aid, user_id=uid)), row['id'], row['name']))
    def load(index):
        db, recipe_id, name = selections[index % 2]
        with db.read_snapshot():
            assert _recipe_detail(db, recipe_id)['name'] == name
            assert db.recipe_get(selections[1 - index % 2][1]) is None
        return True
    with ThreadPoolExecutor(max_workers=20) as pool:
        assert all(pool.map(load, range(50)))


def test_actual_main260_upgrade_preserves_trash_source_and_backup(tmp_path):
    root = Path(__file__).resolve().parents[1]
    exported = subprocess.run(['git', 'show', 'origin/main:app/db.py'], cwd=root, capture_output=True)
    if exported.returncode:
        pytest.skip('origin/main baseline not available in this checkout')
    module_path = tmp_path / 'baseline_main_db.py'
    module_path.write_bytes(exported.stdout)
    spec = importlib.util.spec_from_file_location('app._baseline_main_db', module_path)
    legacy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(legacy)
    if legacy.CURRENT_SCHEMA_VERSION != 260:
        pytest.skip('origin/main has advanced beyond the audited schema 260')
    path = tmp_path / 'upgrade.db'
    old = legacy.Database(path)
    uid = old.user_create('original-owner', 'unchanged-hash', role='admin')
    rid = old.recipe_upsert(url='https://example.test/original', name='Original', type='Hauptgericht',
                           category='Test', folder_path=str(tmp_path / 'original'), description='Originaltext',
                           thumb_filename=None, video_filename=None, source_added_at=123)
    old.recipe_soft_delete(rid)
    current = Database(path)
    row = current.recipe_get(rid)
    assert row['source_url'] == row['deleted_url'] == 'https://example.test/original'
    assert row['deleted_at'] is not None and row['description'] == 'Originaltext'
    assert current.user_get_by_name('original-owner')['id'] == uid
    backups = list((tmp_path / 'backups').glob('pre-migration-v260-to-v*.db'))
    assert len(backups) == 1 and CURRENT_SCHEMA_VERSION > 260
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0] == 260
        assert backup.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
    restored_path = tmp_path / 'restored_main260.db'
    shutil.copyfile(backups[0], restored_path)
    restored = legacy.Database(restored_path)
    assert restored.recipe_get(rid)['deleted_url'] == 'https://example.test/original'
    assert restored.user_get_by_name('original-owner')['password_hash'] == 'unchanged-hash'
    Database(path)
    assert len(list((tmp_path / 'backups').glob('pre-migration-v260-to-v*.db'))) == 1


def test_schema265_repairs_existing_trash_without_revealing_internal_keys(test_db, tmp_path):
    first = _create_recipe(test_db, name='Altbestand', folder_path=str(tmp_path / 'old'))
    second = _create_recipe(test_db, name='Unbekannte Quelle', folder_path=str(tmp_path / 'unknown'))
    test_db.recipe_soft_delete(first['id'])
    test_db.recipe_soft_delete(second['id'])
    with test_db.conn() as c:
        c.execute('UPDATE recipes SET source_url=NULL WHERE id IN (?, ?)', (first['id'], second['id']))
        c.execute("UPDATE recipes SET deleted_url='private-recipe://2/opaque' WHERE id=?", (second['id'],))
        c.execute('DELETE FROM schema_migrations WHERE version=265')
    upgraded = Database(test_db.path)
    assert upgraded.recipe_get(first['id'])['source_url'] == first['url']
    assert upgraded.recipe_get(second['id'])['source_url'] is None


def test_schema265_failure_rolls_back_data_and_new_column(test_db, tmp_path, monkeypatch):
    row = _create_recipe(test_db, name='Rollback', folder_path=str(tmp_path / 'rollback'))
    test_db.recipe_soft_delete(row['id'])
    with test_db.conn() as c:
        c.execute('UPDATE recipes SET source_url=NULL WHERE id=?', (row['id'],))
        c.execute('DELETE FROM schema_migrations WHERE version=265')
        c.execute('ALTER TABLE background_tasks DROP COLUMN recovery_attempts')
    original = Database._migrate
    def interrupted(c):
        original(c)
        raise RuntimeError('synthetic interruption after schema265')
    monkeypatch.setattr(Database, '_migrate', staticmethod(interrupted))
    with pytest.raises(RuntimeError, match='synthetic interruption'):
        Database(test_db.path)
    with sqlite3.connect(test_db.path) as c:
        assert c.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0] == 264
        assert 'recovery_attempts' not in {entry[1] for entry in c.execute('PRAGMA table_info(background_tasks)')}
        assert c.execute('SELECT source_url FROM recipes WHERE id=?', (row['id'],)).fetchone()[0] is None
