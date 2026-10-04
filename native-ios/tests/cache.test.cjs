const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const vm = require('node:vm');
const ts = require('typescript');

// Exercise the shipped module, with a controllable native storage boundary.
const source = fs.readFileSync(require.resolve('../src/lib/cache.ts'), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, esModuleInterop: true },
}).outputText;

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

function harness(overrides = {}) {
  let epoch = 1;
  let namespace = 'server/account-a';
  class ApiError extends Error {
    constructor(message, status) { super(message); this.status = status; }
  }
  const storage = {
    getItem: async () => JSON.stringify({ value: { id: 1 } }),
    setItem: async () => undefined,
    removeItem: async () => undefined,
    ...overrides.storage,
  };
  const api = {
    ApiError,
    api: overrides.api || (async () => { throw new ApiError('offline', 503); }),
    apiCacheNamespace: () => namespace,
    currentApiSessionEpoch: () => epoch,
    assertApiSessionEpochCurrent: request => {
      if (request !== epoch) throw new ApiError('session ended', 401);
    },
  };
  const module = { exports: {} };
  vm.runInNewContext(compiled, {
    module, exports: module.exports,
    require: name => name === './api' ? api : storage,
    Date, Error,
  });
  return {
    cache: module.exports,
    switchAccount() { epoch += 1; namespace = 'server/account-b'; },
  };
}

test('offline request returns the cache for the current session', async () => {
  const { cache } = harness();
  assert.equal((await cache.apiCached('recipe:1', '/api/recipes/1')).id, 1);
});

test('account switch during offline storage read rejects old account data', async () => {
  const started = deferred();
  const read = deferred();
  const h = harness({ storage: { getItem: () => { started.resolve(); return read.promise; } } });
  const result = h.cache.apiCached('recipe:1', '/api/recipes/1');
  await started.promise;
  h.switchAccount();
  read.resolve(JSON.stringify({ value: { private: 'account-a' } }));
  await assert.rejects(result, error => error.status === 401);
});

test('abort during offline storage read does not return cached data', async () => {
  const started = deferred();
  const read = deferred();
  const h = harness({ storage: { getItem: () => { started.resolve(); return read.promise; } } });
  const controller = new AbortController();
  const result = h.cache.apiCached('recipe:1', '/api/recipes/1', controller.signal);
  await started.promise;
  controller.abort();
  read.resolve(JSON.stringify({ value: { id: 1 } }));
  await assert.rejects(result, error => error.name === 'AbortError');
});

test('direct storage read becomes a cache miss after an account switch', async () => {
  const read = deferred();
  const h = harness({ storage: { getItem: () => read.promise } });
  const result = h.cache.readApiCache('recipe:1');
  h.switchAccount();
  read.resolve(JSON.stringify({ value: { id: 1 } }));
  assert.equal(await result, null);
});

test('account switch during successful cache write rejects the server response', async () => {
  const started = deferred();
  const write = deferred();
  const h = harness({
    api: async () => ({ id: 1 }),
    storage: { setItem: () => { started.resolve(); return write.promise; } },
  });
  const result = h.cache.apiCached('recipe:1', '/api/recipes/1');
  await started.promise;
  h.switchAccount();
  write.resolve();
  await assert.rejects(result, error => error.status === 401);
});

test('corrupt cache cleanup stays in the captured account namespace', async () => {
  let removed;
  const removal = deferred();
  const started = deferred();
  const h = harness({ storage: {
    getItem: async () => 'broken JSON',
    removeItem: key => { removed = key; started.resolve(); return removal.promise; },
  } });
  const result = h.cache.apiCached('recipe:1', '/api/recipes/1');
  await started.promise;
  h.switchAccount();
  removal.resolve();
  await assert.rejects(result, error => error.status === 401);
  assert.equal(removed, 'rezepte.cache.v1:server%2Faccount-a:recipe:1');
});
