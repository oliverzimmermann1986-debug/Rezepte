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
  const module = { exports: {} };
  vm.runInNewContext(compiled, {
    module, exports: module.exports, Headers, AbortController, FormData,
    setTimeout, clearTimeout,
    require: name => name === 'expo-constants' ? { expoConfig: {} } : {},
    fetch: async (url, options) => {
      calls.push({ url, options });
      const status = response.status || 200;
      return { status, ok: status < 400, url, headers: new Headers(), text: async () => JSON.stringify(response.body || { ok: true }) };
    },
  });
  module.exports.configureApi('https://rezepte.test', 'guest.signed-test-token', null, 'Gast');
  return { api: module.exports, calls };
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
  h.api.configureApi('https://rezepte.test', 'user-test-token', null, 'Partner');
  await h.api.api('/api/cart/add', { method: 'POST', body: '{"name":"Tomaten"}' });
  assert.equal(h.calls.length, 2);
  assert.equal(h.calls[1].options.headers.get('Authorization'), 'Bearer user-test-token');
});

test('a real user named Gast cannot share the guest cache namespace', () => {
  const h = harness();
  const guestNamespace = h.api.apiCacheNamespace();
  h.api.configureApi('https://rezepte.test', 'user-test-token', null, 'Gast');
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
