const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const vm = require('node:vm');
const ts = require('typescript');

const compiled = ts.transpileModule(fs.readFileSync(require.resolve('../src/lib/api.ts'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, esModuleInterop: true },
}).outputText;

function harness(response = {}) {
  const calls = [];
  const downloads = [];
  const fileSystem = {
    cacheDirectory: 'file://cache/',
    createDownloadResumable: (url, destination, options) => {
      downloads.push({ url, destination, options });
      return { downloadAsync: async () => ({ status: 200 }), cancelAsync: async () => {} };
    },
    deleteAsync: async () => {},
  };
  const module = { exports: {} };
  vm.runInNewContext(compiled, {
    module, exports: module.exports, Headers, AbortController, FormData: class { append() {} },
    setTimeout, clearTimeout,
    require: name => name === 'expo-constants' ? { expoConfig: {} } : fileSystem,
    fetch: async (url, options) => {
      calls.push({ url, options });
      const status = response.status || 200;
      return { status, ok: status < 400, url, redirected: response.redirected, headers: new Headers(), text: async () => response.text ?? JSON.stringify(response.body || { ok: true }) };
    },
  });
  module.exports.configureApi('https://rezepte.test', 'guest.signed-test-token', 'Gast');
  return { api: module.exports, calls, downloads };
}

test('guest reads send the guest token and remain available', async () => {
  const h = harness();
  await h.api.api('/api/recipes');
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].options.headers.get('Authorization'), 'Bearer guest.signed-test-token');
});

test('guest writes are rejected before any network activity', async () => {
  const h = harness();
  for (const method of ['POST', 'PUT', 'PATCH', 'DELETE', 'post']) {
    await assert.rejects(h.api.api('/api/cart/add', { method }), error => error.status === 403);
  }
  assert.equal(h.calls.length, 0);
});

test('guest uploads are rejected before preparing native files or making requests', async () => {
  const h = harness();
  await assert.rejects(h.api.uploadFile('/api/recipes/1/upload-thumbnail', { uri: 'file://demo', name: 'demo.jpg', mimeType: 'image/jpeg' }), error => error.status === 403);
  assert.equal(h.calls.length, 0);
});

test('guest logout and writes after switching to a normal account are available', async () => {
  const h = harness();
  await h.api.api('/api/auth/logout', { method: 'POST' });
  h.api.configureApi('https://rezepte.test', 'user-test-token', 'Partner');
  await h.api.api('/api/cart/add', { method: 'POST', body: '{"name":"Tomaten"}' });
  assert.equal(h.calls.length, 2);
  assert.equal(h.calls[1].options.headers.get('Authorization'), 'Bearer user-test-token');
});

test('a real user named Gast cannot share the guest cache namespace', () => {
  const h = harness();
  const guestNamespace = h.api.apiCacheNamespace();
  h.api.configureApi('https://rezepte.test', 'user-test-token', 'Gast');
  assert.notEqual(h.api.apiCacheNamespace(), guestNamespace);
});

test('registration validation shows the useful error without echoing password input', async () => {
  const h = harness({ status: 422, body: { detail: [{ msg: 'Passwort darf höchstens 72 UTF-8-Bytes enthalten', input: 'private-password' }] } });
  h.api.configureApi('https://rezepte.test', null);
  await assert.rejects(h.api.api('/api/auth/register', { method: 'POST' }), error => {
    assert.equal(error.status, 422);
    assert.match(error.message, /72 UTF-8-Bytes/);
    assert.ok(!error.message.includes('private-password'));
    return true;
  });
});

test('API calls, image headers, uploads and downloads use app authorization without Access headers', async () => {
  const h = harness();
  h.api.configureApi('https://rezepte.test', 'user-test-token', 'Partner');
  await h.api.api('/api/recipes');
  await h.api.uploadFile('/api/recipes/1/upload-thumbnail', { uri: 'file://demo', name: 'demo.jpg', mimeType: 'image/jpeg' });
  await h.api.downloadFileToCache('/api/shopping-list/pdf', 'shopping.pdf');
  const allHeaders = [h.api.apiAuthHeaders(), ...h.calls.map(call => call.options.headers), ...h.downloads.map(call => call.options.headers)];
  assert.equal(allHeaders.length, 4);
  for (const raw of allHeaders) {
    const headers = new Headers(raw);
    assert.equal(headers.get('Authorization'), 'Bearer user-test-token');
    assert.ok([...headers.keys()].every(key => !key.startsWith('cf-access-')));
  }
});

test('legacy pseudo-session never becomes an Authorization header', async () => {
  for (const token of ['cloudflare-access', ' cloudflare-access\n']) {
    const h = harness();
    h.api.configureApi('https://rezepte.test', token);
    await h.api.api('/api/auth/session');
    assert.equal(h.calls[0].options.headers.get('Authorization'), null);
    assert.deepEqual(Object.keys(h.api.apiAuthHeaders()), []);
  }
});

test('HTML and proxy redirects remain useful errors without Access credential instructions', async () => {
  for (const response of [
    { status: 200, text: '<html>Cloudflare Access</html>', redirected: true },
    { status: 302, text: '' },
    { status: 503, text: '<html>Proxy unavailable</html>' },
  ]) {
    const h = harness(response);
    await assert.rejects(h.api.api('/api/recipes'), error => {
      assert.equal(error.status, response.status);
      assert.match(error.message, /umgeleitet|JSON-Antwort/);
      assert.doesNotMatch(error.message, /Cloudflare|Client-ID|Client-Secret/);
      return true;
    });
  }
});
