"""Import privileges with real sessions, tenant scoping and no external jobs."""
from contextlib import contextmanager
from pathlib import Path

import pytest
from itsdangerous import URLSafeTimedSerializer

from app import accounts, auth
from app.config_store import get_config
from app.routes import api_pending
from app.tenant_db import HouseholdDatabase
from app.tenancy import HouseholdScope
from tests.test_native_imports import _jpeg_bytes, _pdf_bytes


@pytest.fixture
def role_client(client, test_db, monkeypatch):
    from app.main import app

    monkeypatch.setattr(auth, '_serializer', lambda: URLSafeTimedSerializer('role-import-test-' * 4))
    for guard in (auth.require_auth, auth.require_admin, auth.require_import):
        app.dependency_overrides.pop(guard, None)
    for name, role in [('operator', 'admin'), ('member', 'user'), ('reader', 'guest'),
                       ('importer', 'full_user'), ('other-importer', 'full_user')]:
        test_db.user_create(name, 'synthetic-hash', role=role)
    queued = []
    monkeypatch.setattr(api_pending, 'enqueue', lambda kind, payload, **kwargs: queued.append((kind, payload)) or len(queued))

    def login(name):
        client.headers['Authorization'] = 'Bearer ' + auth.create_session(name)

    return client, test_db, login, queued


@pytest.mark.parametrize('name', ['member', 'reader'])
@pytest.mark.parametrize('path,payload', [
    ('/api/pending/import-url', {'url': 'https://recipes.example/synthetic'}),
    ('/api/pending/import-file', None),
    ('/api/pending/scan-photo', None),
    ('/api/pending', {'url': 'https://recipes.example/synthetic', 'action': 'save'}),
    ('/api/pending/reanalyze', {'url': 'https://recipes.example/synthetic'}),
])
def test_non_import_roles_rejected_before_queue_or_file_processing(role_client, name, path, payload):
    client, db, login, queued = role_client
    login(name)
    assert client.post(path, json=payload).status_code == 403
    assert queued == []
    assert db.pending_list() == []


def test_full_user_imports_privately_without_administration_or_cross_household_access(role_client):
    client, db, login, queued = role_client
    login('importer')
    session = client.get('/api/session').json()
    assert session['can_import'] is True and session['is_admin'] is False
    assert session['full_access'] is False and session['read_only'] is False
    response = client.post('/api/pending/import-url', json={"ai_processing_consent": "openai-recipe-v1", 'url': 'https://recipes.example/role-test', 'visibility': 'private'})
    assert response.status_code == 200 and response.json()['accepted'] is True
    account_id = client.get('/api/account').json()['id']
    assert queued == [('share_ingest', {'url': 'https://recipes.example/role-test', 'type': 'recipe', 'account_id': account_id})]
    assert len(client.get('/api/pending').json()) == 1
    assert len(client.get('/api/account/imports').json()['items']) == 1
    assert client.get('/api/admin/overview').status_code == 403
    assert client.get('/api/users').status_code == 403
    assert client.post('/api/pending/import-url', json={"ai_processing_consent": "openai-recipe-v1", 'url': 'https://recipes.example/global-denied', 'visibility': 'global'}).status_code == 403
    assert len(queued) == 1
    login('other-importer')
    assert client.get('/api/pending').json() == []
    assert client.post('/api/pending', json={'url': 'https://recipes.example/role-test', 'visibility': 'private', 'action': 'skip'}).status_code == 404
    assert len(db.pending_list()) == 1


def test_role_downgrade_revokes_import_session_and_blocks_new_user_session(role_client):
    client, db, login, queued = role_client
    login('importer')
    db.user_set_role(db.user_get_by_name('importer')['id'], 'user')
    payload = {'url': 'https://recipes.example/denied-after-role-change'}
    assert client.post('/api/pending/import-url', json={**(payload or {}), "ai_processing_consent": "openai-recipe-v1"}).status_code == 401
    login('importer')
    assert client.post('/api/pending/import-url', json={**(payload or {}), "ai_processing_consent": "openai-recipe-v1"}).status_code == 403
    assert queued == []


@pytest.fixture
def synthetic_pipeline(role_client, monkeypatch):
    """Keep real HTTP parsing, role checks and tenancy; replace external work."""
    _, db, _, _ = role_client
    root = Path(get_config().get('paths', 'recipe_dir'))
    temp = Path(get_config().get('paths', 'temp_dir'))
    calls = []

    class ImportJob:
        recipe_dir = root
        temp_dir = temp

        def process_attachment(self, attachment, source):
            calls.append({'operation': 'file', 'account_id': self.db.account_id,
                          'source': source, 'attachment': attachment,
                          'recipe_dir': self.recipe_dir, 'temp_dir': self.temp_dir})
            self.db.pending_add(url=source, content_type='recipe', ai_suggestion={'name': 'Synthetic upload'})
            return {'status': 'pending', 'url': source}

        def attach_pending_photo(self, source, data, suffix, filename):
            calls.append({'operation': 'ocr', 'account_id': self.db.account_id, 'source': source,
                          'data': data, 'suffix': suffix, 'filename': filename})
            self.db.pending_update_suggestion(source, {'name': 'Synthetic OCR', 'source': 'test-ocr'})
            return {'ok': True, 'action': 'still_pending', 'message': 'Synthetic OCR'}

        def reanalyze_pending(self, source):
            calls.append({'operation': 'reanalyze', 'account_id': self.db.account_id, 'source': source})
            self.db.pending_update_suggestion(source, {'name': 'Synthetic reanalysis', 'source': 'test-reanalysis'})
            return {'ok': True, 'action': 'still_pending'}

    original = ImportJob()
    original.db = db
    monkeypatch.setattr(api_pending, 'get_scraper_job', lambda: original)

    @contextmanager
    def available_lock(_name):
        yield object()

    monkeypatch.setattr(api_pending, 'file_lock_or_none', available_lock)
    return calls, original


def _synthetic_file(kind):
    if kind == 'pdf':
        return 'synthetic.pdf', _pdf_bytes(), 'application/pdf'
    return 'synthetic.jpg', _jpeg_bytes('blue'), 'image/jpeg'


def _work_snapshot(db):
    with db.conn() as connection:
        return {table: [tuple(row) for row in connection.execute(f'SELECT * FROM {table} ORDER BY rowid')]
                for table in ('pending', 'background_tasks', 'import_budget_usage')}


@pytest.mark.parametrize('kind', ['photo', 'pdf'])
def test_full_user_file_imports_and_retries_are_private_to_each_household(role_client, synthetic_pipeline, kind):
    client, db, login, queued = role_client
    calls, original = synthetic_pipeline
    upload = _synthetic_file(kind)
    results = {}
    for name in ('importer', 'other-importer'):
        login(name)
        account_id = client.get('/api/account').json()['id']
        for repeat in range(2):
            response = client.post('/api/pending/import-file',
                                   data={"ai_processing_consent": "openai-recipe-v1", 'client_request_id': 'same-upload', 'visibility': 'private',
                                         'owner_account_id': '999999', 'account_id': '999999'},
                                   files={'file': upload})
            assert response.status_code == 200, response.text
            assert response.json()['ok'] is True and response.json()['status'] == 'pending'
            assert response.json()['idempotent_replay'] is bool(repeat)
        results[name] = (account_id, response.json()['url'])
        own = client.get('/api/account/imports').json()['items']
        assert len(own) == 1 and own[0]['url'] == results[name][1]
    assert len(calls) == 2 and results['importer'][1] != results['other-importer'][1]
    assert {item['owner_account_id'] for item in db.pending_list()} == {item[0] for item in results.values()}
    for call in calls:
        assert call['attachment']['data'] == upload[1]
        assert call['attachment']['ext'] == ('.pdf' if kind == 'pdf' else '.jpg')
        assert call['recipe_dir'] == original.recipe_dir.resolve() / '.households' / str(call['account_id'])
        assert call['temp_dir'] == original.temp_dir / 'households' / str(call['account_id'])
    assert original.db is db and queued == []
    with db.conn() as connection:
        assert connection.execute('SELECT COUNT(*) FROM background_tasks').fetchone()[0] == 0


@pytest.mark.parametrize('kind', ['photo', 'pdf'])
@pytest.mark.parametrize('name,visibility', [('member', 'private'), ('reader', 'private'), ('importer', 'global')])
def test_valid_file_uploads_cannot_bypass_role_or_global_import_limits(role_client, synthetic_pipeline, kind, name, visibility):
    client, db, login, queued = role_client
    calls, _ = synthetic_pipeline
    login(name)
    before = _work_snapshot(db)
    response = client.post('/api/pending/import-file', data={"ai_processing_consent": "openai-recipe-v1", 'visibility': visibility},
                           files={'file': _synthetic_file(kind)})
    assert response.status_code == 403, response.text
    assert calls == [] and queued == [] and _work_snapshot(db) == before


@pytest.mark.parametrize('operation', ['ocr', 'reanalyze'])
@pytest.mark.parametrize('target', ['own', 'foreign', 'global'])
@pytest.mark.parametrize('name', ['importer', 'member'])
def test_private_pending_ocr_and_reanalysis_enforce_role_and_ownership(role_client, synthetic_pipeline, operation, target, name):
    client, db, login, queued = role_client
    calls, _ = synthetic_pipeline
    own_id = accounts.view(db, db.user_get_by_name(name)['id'])['id']
    foreign_id = accounts.view(db, db.user_get_by_name('other-importer')['id'])['id']
    urls = {scope: 'https://recipes.example/synthetic-' + scope for scope in ('own', 'foreign', 'global')}
    for scope, owner in (('own', own_id), ('foreign', foreign_id), ('global', None)):
        writer = HouseholdDatabase(db, HouseholdScope(owner), import_owner=owner) if owner else db
        writer.pending_add(url=urls[scope], content_type='recipe', ai_suggestion={'name': scope})
    login(name)
    before = _work_snapshot(db)
    visibility = 'global' if target == 'global' else 'private'
    if operation == 'ocr':
        jpeg = _jpeg_bytes('green')
        response = client.post('/api/pending/scan-photo', data={"ai_processing_consent": "openai-recipe-v1"}, params={'url': urls[target], 'visibility': visibility},
                               files={'file': ('pending.jpg', jpeg, 'image/jpeg')})
    else:
        response = client.post('/api/pending/reanalyze', json={"ai_processing_consent": "openai-recipe-v1", 'url': urls[target], 'visibility': visibility})
    if name == 'member' or target != 'own':
        assert response.status_code == (403 if name == 'member' else 404), response.text
        assert calls == [] and _work_snapshot(db) == before
    else:
        assert response.status_code == 200 and response.json()['action'] == 'still_pending', response.text
        assert len(calls) == 1 and calls[0]['operation'] == operation
        assert calls[0]['account_id'] == own_id and calls[0]['source'] == urls['own']
        if operation == 'ocr':
            assert calls[0]['data'] == jpeg and calls[0]['suffix'] == '.jpg'
        items = {row['source_url']: row for row in db.pending_list()}
        assert items[urls['own']]['ai_suggestion']['name'].startswith('Synthetic')
        assert items[urls['foreign']]['ai_suggestion'] == {'name': 'foreign'}
        assert items[urls['global']]['ai_suggestion'] == {'name': 'global'}
        assert len(items) == 3 and items[urls['own']]['owner_account_id'] == own_id
    assert queued == []
    with db.conn() as connection:
        assert connection.execute('SELECT COUNT(*) FROM background_tasks').fetchone()[0] == 0
