"""Regressions for untrusted mail, paths, HTML identities and preview sizes."""
from pathlib import Path
from types import SimpleNamespace
import ssl
import socket
import threading

import pytest

from app.core import email_processor, recipe_web
from app.jobs.scraper import _sanitize
from app.recipes.manage import sanitize_filename
from app.recipes import image_cache


def test_mail_connection_requires_verified_tls_before_credentials(monkeypatch):
    seen = {}
    class FakeImap:
        def __init__(self, host, port, *, timeout, ssl_context=None):
            seen.update(context=ssl_context, host=host, port=port)
        def login(self, *args):
            seen['login'] = True
        def select(self, folder, *, readonly):
            seen['readonly'] = readonly
        def logout(self):
            seen['logout'] = True
    monkeypatch.setattr(email_processor.imaplib, 'IMAP4_SSL', FakeImap)
    account = email_processor.MailAccount('recipe', {'username':'synthetic', 'password':'synthetic'}, 'recipe')
    with account._connect(readonly=True):
        pass
    assert seen['context'] is not None
    assert seen['context'].verify_mode == ssl.CERT_REQUIRED
    assert seen['context'].check_hostname is True
    assert seen['login'] and seen['readonly'] and seen['logout']


@pytest.mark.parametrize('value', ['.', '..', '...'])
def test_dot_only_analysis_names_cannot_leave_the_recipe_root(tmp_path, value):
    for sanitize in (_sanitize, sanitize_filename):
        component = sanitize(value)
        target = tmp_path / 'households' / '7' / component / component / 'Dish'
        assert component not in {'.', '..', ''}
        assert target.resolve().is_relative_to((tmp_path/'households'/'7').resolve())


@pytest.mark.parametrize('canonical', ['https://trusted.example/recipe', 'https://source.example:8443/recipe'])
def test_recipe_page_cannot_claim_another_origins_identity(monkeypatch, canonical):
    source = 'https://source.example/dish'
    html = f'<link rel="canonical" href="{canonical}"><title>Synthetic</title>'
    monkeypatch.setattr(recipe_web, '_request_following_public_redirects', lambda *args, **kwargs:
                        (SimpleNamespace(status_code=200, headers={'content-type':'text/html'}, text=html), source))
    assert recipe_web.extract_recipe_web_metadata(source, include_thumbnail=False)['canonical_url'] == source


def test_pdf_preview_caps_raster_dimensions_before_rendering(tmp_path, monkeypatch):
    source, target = tmp_path/'synthetic.pdf', tmp_path/'cover.jpg'
    source.write_bytes(b'%PDF-synthetic')
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        Path(str(command[-1]) + '.jpg').write_bytes(b'synthetic cover')
        return SimpleNamespace(returncode=0, stderr='')
    monkeypatch.setattr(image_cache.subprocess, 'run', run)
    assert image_cache.ensure_pdf_first_page(source, target) == target
    assert not list(tmp_path.glob('.*.jpg'))
    command = commands[0]
    assert '-scale-to' in command
    assert int(command[command.index('-scale-to')+1]) <= 1600


@pytest.mark.parametrize('path, content_type', [
    ('/login', b'multipart/form-data; boundary=synthetic'),
    ('/register', b'application/x-www-form-urlencoded'),
    ('/api/auth/register', b'application/json'),
])
def test_non_upload_endpoints_reject_large_bodies_before_parsing(path, content_type):
    import asyncio
    from app.security import UploadSizeLimitMiddleware
    called = []
    async def app(scope, receive, send):
        called.append(True)
    async def receive():
        pytest.fail('A known oversized body must never be read or spooled')
    messages = []
    async def send(message):
        messages.append(message)
    scope = {'type':'http', 'path':path, 'method':'POST',
             'headers':[(b'content-type', content_type), (b'content-length', b'60000000')]}
    asyncio.run(UploadSizeLimitMiddleware(app)(scope, receive, send))
    assert not called
    assert messages[0]['status'] == 413


@pytest.mark.parametrize('trusted,host,accepted', [
    (False, 'localhost', False), (True, '127.0.0.1', False), (True, 'localhost', True),
])
def test_real_imap_tls_handshake_blocks_credentials_for_invalid_certificates(monkeypatch, trusted, host, accepted):
    fixture = Path(__file__).parent/'fixtures'/'synthetic-imap-tls'
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(fixture/'certificate.pem', fixture/'key.pem')
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
    listener.settimeout(5)
    logged_in = threading.Event()
    failures = []
    def server():
        try:
            connection, _ = listener.accept()
            connection.settimeout(5)
            with server_context.wrap_socket(connection, server_side=True) as secure:
                secure.sendall(b'* OK Synthetic IMAP server\r\n')
                with secure.makefile('rb') as stream:
                    while line := stream.readline():
                        tag, command, *_ = line.split()
                        command = command.upper()
                        if command == b'CAPABILITY':
                            secure.sendall(b'* CAPABILITY IMAP4rev1\r\n')
                        elif command == b'LOGIN':
                            logged_in.set()
                        elif command in (b'EXAMINE', b'SELECT'):
                            secure.sendall(b'* 0 EXISTS\r\n')
                        elif command == b'LOGOUT':
                            secure.sendall(b'* BYE Synthetic logout\r\n')
                        secure.sendall(tag+b' OK Completed\r\n')
                        if command == b'LOGOUT':
                            break
        except (ssl.SSLError, ConnectionError) as error:
            # Rejected certificates can close TLS with an alert or a TCP reset.
            if accepted:
                failures.append(error)
        except Exception as error:
            failures.append(error)
        finally:
            listener.close()
    if trusted:
        client_context = ssl.create_default_context(cafile=str(fixture/'certificate.pem'))
        monkeypatch.setattr(email_processor.ssl, 'create_default_context', lambda: client_context)
    thread = threading.Thread(target=server, daemon=True)
    thread.start()
    account = email_processor.MailAccount('recipe', {'imap_host':host, 'imap_port':listener.getsockname()[1],
                                                    'username':'synthetic', 'password':'synthetic'}, 'recipe')
    try:
        if accepted:
            with account._connect(readonly=True):
                pass
        else:
            with pytest.raises(ssl.SSLCertVerificationError):
                with account._connect():
                    pytest.fail('No IMAP session for an untrusted certificate')
    finally:
        thread.join(timeout=7)
    assert not thread.is_alive() and not failures, failures
    assert logged_in.is_set() is accepted


def test_streamed_unknown_length_body_is_rejected_at_the_limit():
    import asyncio
    from app.security import UploadSizeLimitMiddleware
    chunks = iter([{'type':'http.request','body':b'x'*(1024*1024),'more_body':True},
                   {'type':'http.request','body':b'x','more_body':False}])
    async def receive():
        return next(chunks)
    async def app(scope, receive, send):
        await receive()
        await receive()
        pytest.fail('The streamed oversized body must not reach the route')
    messages = []
    async def send(message):
        messages.append(message)
    asyncio.run(UploadSizeLimitMiddleware(app)({'type':'http','path':'/login','headers':[]},receive,send))
    assert messages[0]['status'] == 413


def test_normal_names_stay_usable_and_windows_aliases_are_safe():
    for sanitize in (_sanitize, sanitize_filename):
        assert sanitize('Crème brûlée') == 'Crème_brûlée'
        assert sanitize('CON.txt') == '_CON.txt'
        assert len(sanitize('🍲'*100).encode('utf-8')) <= 180


def test_mail_connection_check_reports_tls_failure_instead_of_empty_success(client, monkeypatch):
    from app.routes import api_test
    class Config:
        def get(self, *args, **kwargs):
            return {'enabled':True,'username':'synthetic','password':'synthetic'}
    monkeypatch.setattr(api_test, 'get_config', lambda: Config())
    monkeypatch.setattr(email_processor.time, 'sleep', lambda seconds: None)
    def reject(*args, **kwargs):
        raise ssl.SSLCertVerificationError('Synthetic invalid certificate')
    monkeypatch.setattr(email_processor.imaplib, 'IMAP4_SSL', reject)
    response = client.post('/api/test/mail',json={'account':'recipe'})
    assert response.status_code == 200
    assert response.json()['ok'] is False
    assert 'SSLCertVerificationError' in response.json()['error']
