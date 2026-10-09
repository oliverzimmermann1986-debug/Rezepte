"""Regressions for untrusted uploads, paths, HTML identities and preview sizes."""
from pathlib import Path
from types import SimpleNamespace
import ssl
import socket
import threading

import pytest

from app.core import recipe_web
from app.jobs.scraper import _sanitize
from app.recipes.manage import sanitize_filename
from app.recipes import image_cache




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
