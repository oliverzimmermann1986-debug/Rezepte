"""Echte Chromium-Formulare gegen einen Server mit eigener Config/DB."""
import os
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

import pytest
import yaml

playwright = pytest.importorskip('playwright.sync_api')
expect = playwright.expect
TEST_PASSWORD = 'synthetic-browser-password-2026'


@pytest.fixture(scope='module')
def real_web(tmp_path_factory):
    from app.auth import hash_password
    root = Path(__file__).resolve().parents[1]
    sandbox = tmp_path_factory.mktemp('real-browser-auth')
    config = yaml.safe_load(Path(os.environ['SCRAPPER_CONFIG']).read_text(encoding='utf-8'))
    for key, value in config['paths'].items():
        if isinstance(value, str):
            config['paths'][key] = str(sandbox / key)
    for key in ('db_path', 'data_dir', 'recipe_dir', 'wedding_dir', 'temp_dir', 'logs_dir', 'recipes_dir'):
        config['paths'][key] = str(sandbox / key)
    config['web'].update(username='synthetic-operator', password=hash_password(TEST_PASSWORD),
                         auth_disabled=False, secret_key='synthetic-browser-secret-' + 'x' * 48,
                         trusted_proxies=[], public_url='')
    config_file = sandbox / 'config.yaml'
    config_file.write_text(yaml.safe_dump(config), encoding='utf-8')
    from app.db import Database
    database = Database(Path(config['paths']['db_path']))
    recipe_dir = Path(config['paths']['recipe_dir']) / 'SyntheticRecipe'
    recipe_dir.mkdir(parents=True)
    database.recipe_upsert(url='https://example.invalid/browser-load', name='SyntheticRecipe',
                           type='Hauptgericht', category='Test', folder_path=str(recipe_dir),
                           description='Test only', thumb_filename=None, video_filename=None, source_added_at=1)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    origin = f'http://127.0.0.1:{port}'
    env = {**os.environ, 'SCRAPPER_CONFIG': str(config_file), 'PYTHONUTF8': '1'}
    env.pop('OPENAI_API_KEY', None)
    # Der echte Server behält seine komplette Auth-/Middleware-/Lifespanlogik.
    # Die einzige Testgrenze lehnt jeden DB-Pfad außerhalb der Sandbox ab,
    # bevor ein Konstruktor Verzeichnisse, Locks oder Datenbanken anlegen kann.
    server_code = '''
from pathlib import Path
import sys
import app.db as db
import uvicorn
original = db.Database.__init__
def guarded(self, path=db.DB_PATH):
    assert Path(path).resolve().is_relative_to(Path(sys.argv[2]).resolve()), 'database escaped browser sandbox'
    original(self, path)
db.Database.__init__ = guarded
uvicorn.run('app.main:app', host='127.0.0.1', port=int(sys.argv[1]), proxy_headers=False, log_level='warning')
'''
    with (sandbox / 'server.log').open('w', encoding='utf-8') as log:
        server = subprocess.Popen([sys.executable, '-c', server_code, str(port), str(sandbox)],
                                  cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 20
            while True:
                if server.poll() is not None:
                    pytest.fail('isolated browser server failed to start: ' + (sandbox / 'server.log').read_text())
                try:
                    with urlopen(origin + '/healthz', timeout=1) as response:
                        assert response.status == 200
                    break
                except (URLError, TimeoutError):
                    assert time.monotonic() < deadline, 'isolated browser server startup timeout'
                    time.sleep(.1)
            with playwright.sync_playwright() as engine:
                browser = engine.chromium.launch(headless=True)
                try:
                    yield browser, origin
                finally:
                    browser.close()
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


def _register(page, origin, name, *, url=None):
    page.goto(url or origin + '/register')
    page.locator('[name=username]').fill(name)
    page.locator('[name=password]').fill(TEST_PASSWORD)
    page.locator('[name=password_confirm]').fill(TEST_PASSWORD)
    with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/register')) as posted:
        page.get_by_role('button', name='Einladung annehmen und Konto erstellen' if url else 'Konto erstellen', exact=True).click()
    assert posted.value.status == 303
    page.wait_for_url(origin + '/account')
    return next(cookie['value'] for cookie in page.context.cookies() if cookie['name'] == 'scrapper_session')


def _form(page, origin, action):
    page.evaluate('''action => { const form=document.createElement('form'); form.method='post'; form.action=action;
                   document.body.appendChild(form); form.submit(); }''', origin + action)


def test_real_forms_register_login_rotate_logout_and_back(real_web):
    browser, origin = real_web
    context = browser.new_context()
    try:
        page = context.new_page()
        first = _register(page, origin, 'browser-person')
        assert context.request.get(origin + '/api/auth/session').json()['username'] == 'browser-person'
        with page.expect_response(lambda response: response.url.endswith('/logout')) as logged_out:
            _form(page, origin, '/logout')
        assert logged_out.value.status == 303
        page.wait_for_url(origin + '/login')
        assert not any(cookie['name'] == 'scrapper_session' for cookie in context.cookies())
        page.go_back()
        assert context.request.get(origin + '/api/auth/session').status == 401
        page.goto(origin + '/login')
        page.locator('[name=username]').fill('browser-person')
        page.locator('[name=password]').fill(TEST_PASSWORD)
        with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/login')) as logged_in:
            page.get_by_role('button', name='Anmelden', exact=True).click()
        assert logged_in.value.status == 303
        page.wait_for_url(origin + '/')
        second = next(cookie['value'] for cookie in context.cookies() if cookie['name'] == 'scrapper_session')
        assert second != first
    finally:
        context.close()


def test_no_referrer_control_is_rejected_but_guest_form_works(real_web):
    browser, origin = real_web
    context = browser.new_context()
    try:
        page = context.new_page()
        page.goto(origin + '/login')
        page.evaluate("const meta=document.createElement('meta');meta.name='referrer';meta.content='no-referrer';document.head.appendChild(meta)")
        with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/login/guest')) as control:
            page.get_by_role('button', name='Als Gast ansehen').click()
        assert control.value.status == 403 and control.value.request.headers.get('origin') == 'null'
        page.goto(origin + '/login')
        with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/login/guest')) as guest:
            page.get_by_role('button', name='Als Gast ansehen').click()
        assert guest.value.status == 303 and guest.value.request.headers.get('origin') == origin
        page.wait_for_url(origin + '/')
        assert context.request.get(origin + '/api/auth/session').json()['role'] == 'guest'
        assert context.request.post(origin + '/api/cart/add', data={'name': 'verboten'}, headers={'Origin': origin}).status == 403
    finally:
        context.close()


def test_invitation_two_browsers_keeps_token_out_of_referrer(real_web):
    browser, origin = real_web
    owner, invited = browser.new_context(), browser.new_context()
    try:
        first, second = owner.new_page(), invited.new_page()
        _register(first, origin, 'browser-owner')
        first.get_by_role('button', name='Zweite Person einladen', exact=True).click()
        link = first.locator('.account-invitation input')
        expect(link).to_be_visible()
        invitation = link.input_value()
        assert invitation.startswith(origin + '/register?invite=')
        requests = []
        second.on('request', lambda request: requests.append(request))
        _register(second, origin, 'browser-member', url=invitation)
        members = owner.request.get(origin + '/api/account').json()['members']
        assert {member['username'] for member in members} == {'browser-owner', 'browser-member'}
        for request in requests:
            if request.headers.get('referer'):
                assert request.headers['referer'] == origin + '/'
        stale = browser.new_context()
        try:
            assert stale.request.post(origin + '/api/auth/register', data={
                'username': 'browser-replay', 'password': TEST_PASSWORD,
                'invitation_token': invitation.split('invite=')[1]}).status == 400
        finally:
            stale.close()
    finally:
        owner.close()
        invited.close()


@pytest.mark.parametrize('action', ['/login', '/register', '/logout'])
def test_foreign_origin_form_is_rejected_without_erasing_session(real_web, action):
    browser, origin = real_web
    context = browser.new_context()
    try:
        page = context.new_page()
        page.goto(origin + '/login')
        page.get_by_role('button', name='Als Gast ansehen').click()
        page.wait_for_url(origin + '/')
        token = next(cookie['value'] for cookie in context.cookies() if cookie['name'] == 'scrapper_session')
        context.route('http://attacker.example/**', lambda route: route.fulfill(content_type='text/html', body='<html><body>Fremde Seite</body></html>'))
        page.goto('http://attacker.example/')
        with page.expect_response(lambda response: response.url == origin + action) as attacked:
            _form(page, origin, action)
        assert attacked.value.status == 403
        assert next(cookie['value'] for cookie in context.cookies() if cookie['name'] == 'scrapper_session') == token
    finally:
        context.close()


def test_real_recipe_detail_load_with_50_parallel_requests(real_web, tmp_path):
    _browser, origin = real_web
    with urlopen(Request(origin + '/api/auth/guest', data=b'{}', headers={'Content-Type': 'application/json'}), timeout=10) as response:
        token = json.load(response)['token']
    def load(_index):
        started = time.perf_counter()
        with urlopen(Request(origin + '/api/recipes/1', headers={'Authorization': 'Bearer ' + token}), timeout=20) as response:
            assert response.status == 200 and json.load(response)['name'] == 'SyntheticRecipe'
        return time.perf_counter() - started
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=50) as pool:
        durations = sorted(pool.map(load, range(50)))
    elapsed = time.perf_counter() - started
    metrics = {'requests': 50, 'parallel_workers': 50, 'http_200': 50,
               'elapsed_seconds': round(elapsed, 3), 'requests_per_second': round(50 / elapsed, 2),
               'p95_seconds': round(durations[47], 3), 'max_seconds': round(durations[-1], 3),
               'platform': sys.platform, 'synthetic_only': True}
    (tmp_path / 'load_metrics.json').write_text(json.dumps(metrics), encoding='utf-8')
