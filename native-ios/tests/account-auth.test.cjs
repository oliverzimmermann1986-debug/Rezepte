const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const vm = require('node:vm');
const ts = require('typescript');

// Run the provider's actual authentication functions with controlled storage
// and hook boundaries. Device rendering and the native keychain remain outside
// this test; failure, focus-refresh and credential-switch ordering are exercised.
const compiled = ts.transpileModule(fs.readFileSync(require.resolve('../src/lib/auth-context.tsx'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, esModuleInterop: true, jsx: ts.JsxEmit.React },
}).outputText;

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function harness(options = {}) {
  let epoch = 0;
  const states = options.startup ? [] : [true, 'guest.original-token', 'https://rezepte.test', 'Gast', false, true];
  let stateIndex = 0;
  const effects = [];
  const startup = deferred();
  const stored = new Map(Object.entries(options.stored || {}));
  const read = [];
  const filesDeleted = [];
  const files = options.files || new Map(options.marker === false ? [] : [['file://documents/.rezepte-install-v1', '1']]);
  const configs = [];
  const requests = [];
  const deleted = [];
  const routes = [];
  const cleared = [];
  class ApiError extends Error { constructor(message, status) { super(message); this.status = status; } }
  const react = {
    createContext: () => ({ Provider: {} }),
    useState: initial => {
      const index = stateIndex++;
      if (!(index in states)) states[index] = initial;
      return [states[index], value => { states[index] = typeof value === 'function' ? value(states[index]) : value; if (index === 0 && states[index]) startup.resolve(); }];
    },
    useRef: initial => ({ current: initial }),
    useEffect: fn => { effects.push(fn); }, useCallback: fn => fn,
    createElement: (_component, props) => ({ props }),
  };
  const dependencies = {
    react,
    'react-native': { AppState: {} },
    'expo-constants': { expoConfig: { extra: { apiUrl: 'https://rezepte.test' } } },
    'expo-file-system/legacy': {
      documentDirectory: 'file://documents/',
      getInfoAsync: async path => ({ exists: files.has(path) }),
      deleteAsync: async path => { filesDeleted.push(path); if (options.fileDeleteFailure?.(path)) throw new Error('file unavailable'); files.delete(path); },
      writeAsStringAsync: async (path, value) => { if (options.fileWriteFailure?.(path)) throw new Error('file unavailable'); files.set(path, value); },
    },
    'expo-image': { Image: {
      clearMemoryCache: async () => { cleared.push('memory'); },
      clearDiskCache: async () => { cleared.push('disk'); },
    } },
    'expo-router': { router: { replace: route => routes.push(route) } },
    'expo-secure-store': {
      getItemAsync: async key => { read.push(key); return stored.get(key) || null; },
      setItemAsync: async (key, value) => {
        if (options.storageFailure && key === 'api-token') throw new Error('keychain unavailable');
        stored.set(key, value);
      },
      deleteItemAsync: async key => {
        deleted.push(key);
        if (options.deleteFailure?.(key)) throw new Error('keychain unavailable');
        stored.delete(key);
      },
    },
    './cache': { clearApiCache: async () => {
      cleared.push('api');
      if (options.cacheFailure) throw new Error('cache unavailable');
    } },
    './browser-auth': {
      fetchIdentityProviders: async () => [],
      providerAuthentication: async (...args) => options.providerRequest ? options.providerRequest(...args) : null,
    },
    './api': {
      ApiError,
      configureApi: (...configuration) => { ++epoch; configs.push(configuration); },
      currentApiSessionEpoch: () => epoch,
      isApiSessionEpochCurrent: request => request === epoch,
      setUnauthorizedHandler: () => {},
      api: async (path, init) => {
        requests.push({ path, init });
        if (options.request) return options.request(path, init);
        return { token: 'registered-session', username: 'Partner', role: 'user', is_admin: false };
      },
    },
  };
  const module = { exports: {} };
  vm.runInNewContext(compiled, {
    module, exports: module.exports,
    require: name => { if (!(name in dependencies)) throw new Error(`Unexpected import: ${name}`); return dependencies[name]; },
    URL, __DEV__: true,
  });
  const render = () => { stateIndex = 0; return module.exports.AuthProvider({ children: null }).props.value; };
  const context = render();
  const start = async () => { effects[0](); await startup.promise; };
  return { context, render, start, states, configs, requests, deleted, routes, cleared, stored, read, filesDeleted, files, ApiError };
}

test('failed registration restores the active guest session', async () => {
  const h = harness({ request: async () => { throw new Error('duplicate username'); } });
  await assert.rejects(h.context.registerAccount('https://rezepte.test', 'Partner', 'password-test'), /duplicate username/);
  assert.equal(h.configs.at(-1)[1], 'guest.original-token');
  assert.equal(h.configs.at(-1)[2], 'Gast');
  assert.equal(h.states[1], 'guest.original-token');
  assert.equal(h.deleted.length, 0);
});

test('fresh legacy backend responses cannot activate or persist login, guest or registration sessions', async () => {
  for (const token of ['cloudflare-access', '  cloudflare-access\n']) {
    for (const mode of ['login', 'guest', 'register']) {
      const h = harness({ startup: true, request: async () => ({ token, username: 'Legacy admin', role: 'admin', is_admin: true }) });
      const operation = mode === 'guest'
        ? h.context.signInAsGuest('https://rezepte.test')
        : mode === 'register'
          ? h.context.registerAccount('https://rezepte.test', 'Partner', 'password-test')
          : h.context.signIn('https://rezepte.test', 'Partner', 'password-test');
      await assert.rejects(operation, error => error.status === 502 && /keine gültige App-Sitzung/.test(error.message));
      assert.equal(h.requests[0].path, `/api/auth/${mode}`);
      assert.equal(h.states[1], null);
      assert.equal(h.states[4], false);
      assert.equal(h.states[5], false);
      assert.equal(h.stored.size, 0);
      assert.equal(h.configs.at(-1)[1], null);
      assert.deepEqual(h.cleared, []);
      assert.deepEqual(h.deleted, []);
      assert.deepEqual(h.routes, []);
    }
  }
});

test('a legacy registration result preserves the prior guest session and private caches', async () => {
  const stored = { 'api-token': 'guest.original-token', 'rezepte.username': 'Gast' };
  const h = harness({ stored, request: async () => ({ token: ' cloudflare-access ', username: 'Legacy admin', role: 'admin', is_admin: true }) });
  await assert.rejects(h.context.registerAccount('https://rezepte.test', 'Partner', 'password-test'), /keine gültige App-Sitzung/);
  assert.equal(h.configs.at(-1)[1], 'guest.original-token');
  assert.equal(h.states[1], 'guest.original-token');
  assert.equal(h.states[3], 'Gast');
  assert.equal(h.states[4], false);
  assert.equal(h.states[5], true);
  assert.deepEqual(Object.fromEntries(h.stored), stored);
  assert.deepEqual(h.cleared, []);
  assert.deepEqual(h.deleted, []);
  assert.deepEqual(h.routes, []);
});

test('focus refresh cannot log out the guest while registration is pending', async () => {
  const pending = deferred();
  const h = harness({ request: () => pending.promise });
  const registration = h.context.registerAccount('https://rezepte.test', 'Partner', 'password-test', 'invite-test');
  await h.context.refreshSession();
  assert.equal(h.requests.length, 1);
  assert.equal(h.requests[0].path, '/api/auth/register');
  assert.equal(JSON.parse(h.requests[0].init.body).invitation_token, 'invite-test');
  pending.resolve({ token: 'registered-session', username: 'Partner', role: 'user' });
  await registration;
  assert.equal(h.states[1], 'registered-session');
  assert.equal(h.states[5], false);
});

test('switching from guest to login deletes the session and legacy credentials', async () => {
  const h = harness();
  await h.context.returnToLogin();
  assert.deepEqual(h.deleted.sort(), ['api-token', 'cloudflare-client-id', 'cloudflare-client-secret', 'rezepte.username']);
  assert.equal(h.states[1], null);
  assert.equal(h.configs.at(-1)[1], null);
  assert.equal(h.configs.at(-1).length, 2);
  assert.equal(h.routes.at(-1), '/login');
});

test('keychain failure after registration leaves no active or partially stored session', async () => {
  const h = harness({ storageFailure: true });
  await assert.rejects(h.context.registerAccount('https://rezepte.test', 'Partner', 'password-test'), /keychain unavailable/);
  assert.equal(h.states[1], null);
  assert.equal(h.states[5], false);
  assert.equal(h.configs.at(-1)[1], null);
  assert.ok(h.deleted.includes('api-token') && h.deleted.includes('cloudflare-client-secret'));
  assert.equal(h.routes.at(-1), '/login');
});

test('expired session clears private image caches even when the API cache fails', async () => {
  const h = harness({ cacheFailure: true, request: async () => { throw new h.ApiError('expired', 401); } });
  await h.context.refreshSession();
  assert.equal(h.states[1], null);
  assert.deepEqual(h.cleared.sort(), ['api', 'disk', 'memory']);
  assert.equal(h.routes.at(-1), '/login');
  assert.ok(h.deleted.includes('api-token'));
});

test('network failure preserves the session and private caches for retry', async () => {
  const h = harness({ request: async () => { throw new Error('offline'); } });
  await h.context.refreshSession();
  assert.equal(h.states[1], 'guest.original-token');
  assert.deepEqual(h.cleared, []);
  assert.deepEqual(h.deleted, []);
});

function legacyStored(token = 'user-existing-session') {
  return {
    'api-token': token,
    'rezepte.server': 'https://rezepte.test',
    'rezepte.username': 'Partner',
    'cloudflare-client-id': 'retired-id',
    'cloudflare-client-secret': 'retired-secret',
  };
}

test('upgrade removes legacy credentials without reading them or replacing a valid user session', async () => {
  const h = harness({ startup: true, stored: legacyStored() });
  await h.start();
  assert.equal(h.states[0], true);
  assert.equal(h.states[1], 'user-existing-session');
  assert.equal(h.stored.get('api-token'), 'user-existing-session');
  assert.equal(h.requests[0].path, '/api/auth/session');
  assert.deepEqual(h.configs[0], ['https://rezepte.test', 'user-existing-session', 'Partner']);
  assert.deepEqual(h.deleted.sort(), ['cloudflare-client-id', 'cloudflare-client-secret']);
  assert.ok(h.read.every(key => !key.startsWith('cloudflare')));
  assert.deepEqual(h.cleared, []);
});

test('upgrade keeps an offline guest session and cached recipes', async () => {
  const h = harness({ startup: true, stored: legacyStored('guest.existing-session'), request: async () => { throw new Error('offline'); } });
  await h.start();
  assert.equal(h.states[1], 'guest.existing-session');
  assert.equal(h.states[5], true);
  assert.equal(h.stored.get('api-token'), 'guest.existing-session');
  assert.deepEqual(h.cleared, []);
});

test('upgrade rejects the legacy pseudo-session before any API request and purges private caches', async () => {
  const h = harness({ startup: true, stored: legacyStored('cloudflare-access') });
  await h.start();
  assert.equal(h.states[1], null);
  assert.equal(h.states[3], '');
  assert.equal(h.states[4], false);
  assert.equal(h.requests.length, 0);
  assert.equal(h.configs[0][1], null);
  assert.ok(!h.stored.has('api-token'));
  assert.ok(!h.stored.has('rezepte.username'));
  assert.ok(!h.stored.has('cloudflare-client-secret'));
  assert.equal(h.stored.get('rezepte.server'), 'https://rezepte.test');
  assert.deepEqual(h.cleared.sort(), ['api', 'disk', 'memory']);
});

test('failed credential cleanup and retry preserve the valid signed-in session', async () => {
  let rejectLegacy = true;
  const h = harness({ startup: true, stored: legacyStored(), deleteFailure: key => rejectLegacy && key === 'cloudflare-client-secret' });
  await h.start();
  assert.equal(h.states[1], 'user-existing-session');
  assert.equal(h.render().authCleanupPending, true);
  assert.equal(h.stored.get('api-token'), 'user-existing-session');
  assert.equal(h.filesDeleted.length, 0);
  rejectLegacy = false;
  await h.render().retryAuthCleanup();
  assert.equal(h.render().authCleanupPending, false);
  assert.equal(h.states[1], 'user-existing-session');
  assert.equal(h.stored.get('api-token'), 'user-existing-session');
  assert.ok(!h.stored.has('cloudflare-client-secret'));
  assert.ok(!h.deleted.includes('api-token'));
});

test('an undeletable whitespace-padded pseudo-session remains unusable and retries cleanup on next startup', async () => {
  const h = harness({ startup: true, stored: legacyStored(' cloudflare-access\n'), deleteFailure: key => key === 'api-token' });
  await h.start();
  assert.equal(h.states[1], null);
  assert.equal(h.render().authCleanupPending, true);
  assert.equal(h.requests.length, 0);
  assert.equal(h.configs[0][1], null);
  assert.ok(h.filesDeleted.some(path => path.endsWith('.rezepte-install-v1')));
});

test('logout removes all legacy credentials and revokes the app session', async () => {
  const h = harness({ stored: legacyStored() });
  await h.context.signOut();
  assert.equal(h.states[1], null);
  assert.equal(h.requests[0].path, '/api/auth/logout');
  assert.ok(h.deleted.includes('cloudflare-client-id'));
  assert.ok(h.deleted.includes('cloudflare-client-secret'));
  assert.equal(h.stored.size, 0);
  assert.deepEqual(h.cleared.sort(), ['api', 'disk', 'memory']);
});

test('failed keychain and install-marker deletion leaves durable logout intent that blocks restart', async () => {
  const options = { stored: legacyStored(), deleteFailure: key => key === 'api-token', fileDeleteFailure: path => path.endsWith('.rezepte-install-v1') };
  const h = harness(options);
  await h.context.signOut();
  assert.equal(h.states[1], null);
  assert.equal(h.files.has('file://documents/.rezepte-logout-pending'), true);
  const restarted = harness({ ...options, startup: true, files: h.files, stored: Object.fromEntries(h.stored) });
  await restarted.start();
  assert.equal(restarted.states[1], null);
  assert.equal(restarted.requests.length, 0);
  assert.equal(restarted.read.includes('api-token'), false);
  assert.equal(restarted.render().authCleanupPending, true);
});

test('successful cleanup after restart consumes logout intent without restoring old token', async () => {
  const files = new Map([['file://documents/.rezepte-install-v1', '1'], ['file://documents/.rezepte-logout-pending', '1']]);
  const h = harness({ startup: true, stored: legacyStored(), files });
  await h.start();
  assert.equal(h.states[1], null);
  assert.equal(h.requests.length, 0);
  assert.equal(h.files.has('file://documents/.rezepte-logout-pending'), false);
  assert.equal(h.stored.has('api-token'), false);
});

test('server logout failure is visible after local logout and all-device logout uses its own endpoint', async () => {
  const h = harness({ request: async () => { throw new Error('offline'); } });
  await h.context.signOut({ all: true });
  assert.equal(h.states[1], null);
  assert.equal(h.requests[0].path, '/api/auth/logout-all');
  assert.match(h.render().sessionWarning, /Serverabmeldung konnte nicht bestätigt/);
});

test('already revoked session cleanup does not issue another logout or claim a network failure', async () => {
  const h = harness();
  await h.context.signOut({ localOnly: true, notice: 'Passwort gespeichert.' });
  assert.equal(h.requests.length, 0);
  assert.equal(h.states[1], null);
  assert.equal(h.render().sessionWarning, 'Passwort gespeichert.');
});

test('cancelled provider login restores the existing guest session without purging credentials', async () => {
  const h = harness({ providerRequest: async () => null, stored: legacyStored('guest.original-token') });
  await h.context.signInWithProvider('https://rezepte.test', 'apple');
  assert.equal(h.configs.at(-1)[1], 'guest.original-token');
  assert.equal(h.states[1], 'guest.original-token');
  assert.equal(h.deleted.length, 0);
});

test('provider exchange uses the same secure storage and role setup as password login', async () => {
  const h = harness({ providerRequest: async (provider, intent, invitation) => {
    assert.equal(provider, 'google'); assert.equal(intent, 'login'); assert.equal(invitation, 'synthetic-invite');
    return { token: 'provider-session', username: 'Federated', role: 'user' };
  } });
  await h.context.signInWithProvider('https://rezepte.test', 'google', 'synthetic-invite');
  assert.equal(h.stored.get('api-token'), 'provider-session');
  assert.equal(h.states[3], 'Federated');
  assert.equal(h.states[4], false);
  assert.equal(h.states[5], false);
});

test('provider login cannot activate a pseudo-session or link an anonymous guest', async () => {
  const h = harness({ providerRequest: async () => ({ token: 'cloudflare-access', username: 'Bad', role: 'admin' }) });
  await assert.rejects(h.context.signInWithProvider('https://rezepte.test', 'apple'), /keine gültige App-Sitzung/);
  await assert.rejects(h.context.linkProvider('apple'), /zuerst mit deinem Konto/);
  assert.equal(h.states[1], 'guest.original-token');
  assert.equal(h.stored.size, 0);
});
