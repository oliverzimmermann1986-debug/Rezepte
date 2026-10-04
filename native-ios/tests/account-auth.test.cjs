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
  const states = [true, 'guest.original-token', 'https://rezepte.test', 'Gast', false, true, 'device-id', 'device-secret'];
  let stateIndex = 0;
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
      return [states[index], value => { states[index] = value; }];
    },
    useRef: initial => ({ current: initial }),
    useEffect: () => {}, useCallback: fn => fn,
    createElement: (_component, props) => ({ props }),
  };
  const dependencies = {
    react,
    'react-native': { AppState: {} },
    'expo-constants': { expoConfig: { extra: { apiUrl: 'https://rezepte.test' } } },
    'expo-file-system/legacy': { documentDirectory: null },
    'expo-image': { Image: {
      clearMemoryCache: async () => { cleared.push('memory'); },
      clearDiskCache: async () => { cleared.push('disk'); },
    } },
    'expo-router': { router: { replace: route => routes.push(route) } },
    'expo-secure-store': {
      setItemAsync: async (key, value) => { if (options.storageFailure && key === 'api-token') throw new Error('keychain unavailable'); },
      deleteItemAsync: async key => { deleted.push(key); },
    },
    './cache': { clearApiCache: async () => {
      cleared.push('api');
      if (options.cacheFailure) throw new Error('cache unavailable');
    } },
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
  const context = module.exports.AuthProvider({ children: null }).props.value;
  return { context, states, configs, requests, deleted, routes, cleared, ApiError };
}

test('failed registration restores the active guest and device access', async () => {
  const h = harness({ request: async () => { throw new Error('duplicate username'); } });
  await assert.rejects(h.context.registerAccount('https://rezepte.test', 'Partner', 'password-test', 'device-id', 'device-secret'), /duplicate username/);
  assert.equal(h.configs.at(-1)[1], 'guest.original-token');
  assert.equal(h.configs.at(-1)[2].clientSecret, 'device-secret');
  assert.equal(h.states[1], 'guest.original-token');
  assert.equal(h.deleted.length, 0);
});

test('focus refresh cannot log out the guest while registration is pending', async () => {
  const pending = deferred();
  const h = harness({ request: () => pending.promise });
  const registration = h.context.registerAccount('https://rezepte.test', 'Partner', 'password-test', 'device-id', 'device-secret');
  await h.context.refreshSession();
  assert.equal(h.requests.length, 1);
  assert.equal(h.requests[0].path, '/api/auth/register');
  pending.resolve({ token: 'registered-session', username: 'Partner', role: 'user' });
  await registration;
  assert.equal(h.states[1], 'registered-session');
  assert.equal(h.states[5], false);
});

test('switching from guest to login deletes the session and retains device credentials', async () => {
  const h = harness();
  await h.context.returnToLogin();
  assert.deepEqual(h.deleted.sort(), ['api-token', 'rezepte.username']);
  assert.equal(h.states[1], null);
  assert.equal(h.states[6], 'device-id');
  assert.equal(h.states[7], 'device-secret');
  assert.equal(h.configs.at(-1)[1], null);
  assert.equal(h.configs.at(-1)[2].clientSecret, 'device-secret');
  assert.equal(h.routes.at(-1), '/login');
});

test('keychain failure after registration leaves no active or partially stored session', async () => {
  const h = harness({ storageFailure: true });
  await assert.rejects(h.context.registerAccount('https://rezepte.test', 'Partner', 'password-test', 'device-id', 'device-secret'), /keychain unavailable/);
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
