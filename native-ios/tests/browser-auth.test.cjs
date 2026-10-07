const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const test = require('node:test');
const vm = require('node:vm');
const ts = require('typescript');

const compile = path => ts.transpileModule(fs.readFileSync(require.resolve(path), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, esModuleInterop: true, target: ts.ScriptTarget.ES2020 },
}).outputText;
const compiled = compile('../src/lib/browser-auth.ts');

function harness(options = {}) {
  const requests = [], browserCalls = [];
  let epoch = 1;
  class ApiError extends Error { constructor(message, status) { super(message); this.status = status; } }
  const module = { exports: {} };
  const dependencies = {
    './api': {
      ApiError, currentApiSessionEpoch: () => epoch, isApiSessionEpochCurrent: value => value === epoch,
      api: async (path, init) => {
        const body = JSON.parse(init.body); requests.push({ path, body });
        if (path.endsWith('/start')) return { authorization_url: options.url || 'https://accounts.google.com/o/oauth2/v2/auth?state=synthetic', flow_id: 'flow-test' };
        return { token: 'synthetic-session', username: 'Partner', role: 'user' };
      },
    },
    'expo-crypto': {
      CryptoDigestAlgorithm: { SHA256: 'SHA-256' }, CryptoEncoding: { BASE64: 'base64' },
      getRandomBytesAsync: async count => { assert.equal(count, 32); return new Uint8Array(crypto.randomBytes(count)); },
      digestStringAsync: async (_algorithm, value) => crypto.createHash('sha256').update(value).digest('base64'),
    },
    'expo-web-browser': {
      openAuthSessionAsync: async (...args) => {
        browserCalls.push(args);
        if (options.expire) ++epoch;
        return options.result || { type: 'success', url: 'de.mausbaeren.rezepte://auth/callback?flow_id=flow-test&code=' + 'x'.repeat(43) };
      },
    },
  };
  vm.runInNewContext(compiled, {
    module, exports: module.exports, URL,
    fetch: async () => ({ ok: true, redirected: false, json: async () => ({ providers: options.providers || [] }) }),
    require: name => { if (!(name in dependencies)) throw new Error(name); return dependencies[name]; },
  });
  return { ...module.exports, requests, browserCalls };
}

test('native provider flow binds a cryptographic PKCE challenge, callback flow and one-use exchange', async () => {
  const h = harness();
  const result = await h.providerAuthentication('google', 'login', 'synthetic-invitation');
  assert.equal(result.token, 'synthetic-session');
  assert.equal(h.requests[0].body.platform, 'native');
  assert.equal(h.requests[0].body.intent, 'login');
  assert.equal(h.requests[0].body.invitation_token, 'synthetic-invitation');
  assert.match(h.requests[0].body.code_challenge, /^[a-zA-Z0-9_-]{43}$/);
  const verifier = h.requests[1].body.code_verifier;
  assert.match(verifier, /^[a-f0-9]{64}$/);
  assert.equal(h.requests[0].body.code_challenge, crypto.createHash('sha256').update(verifier).digest('base64url'));
  assert.equal(h.requests[1].path, '/api/auth/exchange');
  assert.equal(h.requests[1].body.code, 'x'.repeat(43));
  assert.equal(h.browserCalls[0][1], 'de.mausbaeren.rezepte://auth/callback');
  assert.equal(h.browserCalls[0][2].preferEphemeralSession, true);
});

test('provider cancellation performs no exchange', async () => {
  const h = harness({ result: { type: 'cancel' } });
  assert.equal(await h.providerAuthentication('google', 'link'), null);
  assert.equal(h.requests.length, 1);
  assert.equal(h.requests[0].body.intent, 'link');
});

test('new provider linking sends current password only in the POST body', async () => {
  const h = harness();
  await h.providerAuthentication('google', 'link', '', 'synthetic-current-password');
  assert.equal(h.requests[0].body.current_password, 'synthetic-current-password');
  assert.ok(h.browserCalls[0].every(value => typeof value !== 'string' || !value.includes('synthetic-current-password')));
  assert.equal('current_password' in h.requests[1].body, false);
});

test('wrong provider host, callback scheme, flow and missing code cannot activate a session', async () => {
  for (const options of [
    { url: 'https://accounts.google.com.example.invalid/auth' },
    { url: 'http://accounts.google.com/auth' },
    { url: 'https://accounts.google.com:443/auth' },
    { url: 'https://accounts.google.com/auth#fragment' },
    { result: { type: 'success', url: 'other://auth/callback?flow_id=flow-test&code=x' } },
    { result: { type: 'success', url: 'de.mausbaeren.rezepte://auth/callback?flow_id=other&code=x' } },
    { result: { type: 'success', url: 'de.mausbaeren.rezepte://auth/callback?flow_id=flow-test&error=denied' } },
    { expire: true },
  ]) {
    const h = harness(options);
    await assert.rejects(h.providerAuthentication('google', 'login'));
    assert.equal(h.requests.length, 1);
  }
});

test('ambiguous or decorated native callbacks are rejected before exchange', async () => {
  const query = 'flow_id=flow-test&code=' + 'x'.repeat(43);
  for (const url of [
    `de.mausbaeren.rezepte://auth/callback?${query}&flow_id=flow-test`,
    `de.mausbaeren.rezepte://auth/callback?${query}&code=${'x'.repeat(43)}`,
    `de.mausbaeren.rezepte://auth/callback?${query}&error=cancelled`,
    `de.mausbaeren.rezepte://user@auth/callback?${query}`,
    `de.mausbaeren.rezepte://user:pass@auth/callback?${query}`,
    `de.mausbaeren.rezepte://auth:443/callback?${query}`,
    `de.mausbaeren.rezepte://auth/callback?${query}#fragment`,
    'de.mausbaeren.rezepte://auth/callback?flow_id=flow-test&error=cancelled&error=cancelled',
  ]) {
    const h = harness({ result: { type: 'success', url } });
    await assert.rejects(h.providerAuthentication('google', 'login'));
    assert.equal(h.requests.length, 1);
  }
});

test('one flow-bound cancellation response does not exchange a code', async () => {
  const h = harness({ result: { type: 'success', url: 'de.mausbaeren.rezepte://auth/callback?flow_id=flow-test&error=cancelled' } });
  assert.equal(await h.providerAuthentication('google', 'login'), null);
  assert.equal(h.requests.length, 1);
});

test('disabled and unknown providers never appear in the native login', async () => {
  const h = harness({ providers: [{ id: 'apple', name: 'Apple', enabled: true }, { id: 'google', enabled: false }, { id: 'other', enabled: true }] });
  assert.deepEqual(Array.from(await h.fetchIdentityProviders('https://rezepte.test'), item => item.id), ['apple']);
});

test('password policy counts Unicode scalar values and exact UTF-8 bytes', () => {
  const module = { exports: {} };
  vm.runInNewContext(compile('../src/lib/account-management.ts'), { module, exports: module.exports });
  const { passwordProblem } = module.exports;
  assert.equal(passwordProblem('a'.repeat(72)), '');
  assert.equal(passwordProblem('😀'.repeat(18)), '');
  assert.match(passwordProblem('😀'.repeat(9)), /10 Zeichen/);
  assert.match(passwordProblem('😀'.repeat(19)), /72 UTF-8/);
  assert.match(passwordProblem('a'.repeat(73)), /72 UTF-8/);
  assert.match(passwordProblem('long-password', 'other-password'), /stimmen nicht/);
});
