"""Installed-code checks with temporary data; no real accounts or network."""
from __future__ import annotations

import asyncio
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from unittest.mock import patch

import yaml


def main():
    app_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(app_root))
    with tempfile.TemporaryDirectory(prefix='rezepte-load-auth-probe-') as temporary:
        root = Path(temporary).resolve()
        cfg = yaml.safe_load((app_root / 'config/config.example.yaml').read_text(encoding='utf-8'))
        for key, value in list(cfg['paths'].items()):
            if isinstance(value, str):
                cfg['paths'][key] = str(root / key)
        for key in ('db_path', 'data_dir', 'recipe_dir', 'temp_dir', 'wedding_dir', 'logs_dir'):
            cfg['paths'][key] = str(root / key)
        for account in cfg['mail'].values():
            account.update(enabled=False, password='')
        cfg['ai']['openai']['api_key'] = ''
        cfg['ai']['image_generation']['enabled'] = False
        cfg['ai']['video_fallback']['enabled'] = False
        cfg['ai']['auto_translate'] = False
        cfg['web'].update(auth_disabled=False, username='synthetic-operator',
                          password='synthetic-probe-unused-password', secret_key='synthetic-probe-' + 's' * 48,
                          trusted_proxies=[])
        config_file = root / 'config.yaml'
        config_file.write_text(yaml.safe_dump(cfg), encoding='utf-8')
        os.environ['SCRAPPER_CONFIG'] = str(config_file)
        from app import __version__, accounts, auth, db as db_module, security
        from app.db import CURRENT_SCHEMA_VERSION, Database, get_db
        from app.config_store import get_config
        from app.jobs import locks
        from app.routes import api_auth, api_recipes
        from app.tenancy import HouseholdScope, household_context
        from starlette.requests import Request
        from fastapi import HTTPException
        checks = {}
        database = get_db()
        assert database.path.resolve() == (root / 'db_path').resolve()
        assert locks.lock_dir().is_relative_to(root)
        checks['singleton_and_locks_use_temporary_config'] = True
        database.user_create('nat-person', auth.hash_password('synthetic-only-password'))
        request = Request({'type': 'http', 'method': 'POST', 'path': '/api/auth/login', 'scheme': 'http',
                           'headers': [(b'host', b'probe')], 'client': ('192.0.2.15', 1000),
                           'server': ('probe', 80), 'query_string': b''})
        for _index in range(5):
            try:
                api_auth.native_login(api_auth.NativeLogin(username='blocked-person', password='wrong'), request)
                raise AssertionError('Wrong password accepted')
            except HTTPException as exc:
                assert exc.status_code == 401
        other = api_auth.native_login(api_auth.NativeLogin(username='nat-person', password='synthetic-only-password'), request)
        assert auth.session_user(other['token']) == 'nat-person'
        try:
            api_auth.native_login(api_auth.NativeLogin(username='BLOCKED-PERSON', password='wrong'), request)
            raise AssertionError('Actor limit missing')
        except HTTPException as exc:
            assert exc.status_code == 429 and int(exc.headers['Retry-After']) > 0
        checks['actor_lock_keeps_other_nat_account_usable'] = True
        spoof = Request({**request.scope, 'headers': [(b'cf-connecting-ip', b'198.51.100.55'),
                                                     (b'x-forwarded-for', b'198.51.100.66')]})
        assert security.client_ip(spoof) == '192.0.2.15'
        checks['direct_proxy_headers_do_not_change_identity'] = True
        get_config().set('web', 'trusted_proxies', ['127.0.0.1/32'])
        forwarded = Request({**request.scope, 'client': ('127.0.0.1', 1234),
                             'headers': [(b'x-forwarded-for', b'198.51.100.12, 192.0.2.20, 127.0.0.1')]})
        assert security.client_ip(forwarded) == '192.0.2.20'
        checks['trusted_proxy_ignores_untrusted_left_prefix'] = True
        task_id = database.background_task_enqueue('share_ingest', {'url': 'https://example.invalid/synthetic'})
        for _index in range(7):
            assert database.background_task_claim_next()['id'] == task_id
            database.background_task_retry(task_id, delay_seconds=0, error='temporary')
        database.background_task_claim_next()
        assert database.background_tasks_recover() == 1
        assert database.background_task_get(task_id)['recovery_attempts'] == 1
        checks['regular_retries_do_not_exhaust_crash_recovery'] = True
        uid = database.user_get_by_name('nat-person')['id']
        aid = accounts.view(database, uid)['id']
        folder = root / 'recipe_dir' / 'SyntheticRecipe'
        folder.mkdir(parents=True)
        rid = database.recipe_upsert(url='https://example.invalid/synthetic', name='SyntheticRecipe',
                                    type='Hauptgericht', category='Test', folder_path=str(folder), description=None,
                                    thumb_filename=None, video_filename=None, source_added_at=1)
        with database.conn() as c:
            c.execute('UPDATE recipes SET owner_account_id=? WHERE id=?', (aid, rid))
        original_connect = sqlite3.connect
        opened = []
        def count(*args, **kwargs):
            opened.append(args[0])
            return original_connect(*args, **kwargs)
        with household_context(HouseholdScope(aid, user_id=uid)), patch.object(sqlite3, 'connect', side_effect=count):
            assert api_recipes.get_recipe(rid)['name'] == 'SyntheticRecipe'
        assert len(opened) == 1
        checks['detail_uses_one_read_connection'] = True
        with database.read_snapshot():
            try:
                database.recipe_set_servings(rid, 4)
                raise AssertionError('Read snapshot permitted a write')
            except sqlite3.OperationalError:
                pass
        checks['snapshot_rejects_mutation'] = True
        database.recipe_soft_delete(rid)
        with database.conn() as c:
            c.execute('UPDATE recipes SET source_url=NULL WHERE id=?', (rid,))
            c.execute('DELETE FROM schema_migrations WHERE version=265')
            c.execute('ALTER TABLE background_tasks DROP COLUMN recovery_attempts')
        upgraded = Database(database.path)
        assert upgraded.recipe_get(rid)['source_url'] == 'https://example.invalid/synthetic'
        backup = list((root / 'backups').glob('pre-migration-v264-to-v265-*.db'))
        assert len(backup) == 1
        with closing(sqlite3.connect(backup[0])) as c:
            assert c.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0] == 264
            assert c.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
        checks['trash_source_backfill_has_verified_upgrade_backup'] = True
        with upgraded.conn() as writer:
            writer.execute('BEGIN IMMEDIATE')
            async def locked_app(scope, receive, send):
                with upgraded.conn() as c:
                    c.execute('PRAGMA busy_timeout=100')
                    c.execute('INSERT INTO shopping_cart(name,canonical_name) VALUES(?,?)', ('Test', 'test'))
            messages = []
            async def send(message):
                messages.append(message)
            async def receive():
                return {'type': 'http.request', 'body': b'', 'more_body': False}
            asyncio.run(security.DatabaseBusyMiddleware(locked_app)({'type': 'http', 'path': '/synthetic'}, receive, send))
        assert messages[0]['status'] == 503 and (b'retry-after', b'1') in messages[0]['headers']
        checks['sqlite_contention_returns_503_retry_after'] = True
        checks['probe_is_synthetic_without_network'] = database.path.is_relative_to(root)
        assert all(checks.values())
        db_module._db = None
        result = {'version': __version__, 'schema': CURRENT_SCHEMA_VERSION, 'checks': checks}
    print(json.dumps(result))


if __name__ == '__main__':
    main()
