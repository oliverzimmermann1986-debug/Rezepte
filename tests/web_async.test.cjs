// Run the real web methods with controlled responses; no server or user data.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../app/static');

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function createApp(overrides = {}) {
  const context = vm.createContext({
    window: {}, navigator: {}, console, AbortController, URLSearchParams, URL, TextEncoder, confirm: () => true,
    setTimeout, clearTimeout, ...overrides,
  });
  for (const name of fs.readdirSync(path.join(root, 'features'))) {
    if (name.endsWith('.js')) vm.runInContext(fs.readFileSync(path.join(root, 'features', name), 'utf8'), context);
  }
  vm.runInContext(fs.readFileSync(path.join(root, 'app.js'), 'utf8'), context);
  const app = context.scrapperApp();
  app.showToast = () => {};
  return app;
}

async function flush() {
  for (let i = 0; i < 8; i++) await Promise.resolve();
}

test('backup button posts once and reloads the resulting inventory', async () => {
  const app = createApp();
  const pending = deferred();
  const calls = [];
  app.api = (method, url) => { calls.push([method, url]); return pending.promise; };
  let reloaded = 0;
  app.loadMaintenance = async () => { ++reloaded; };
  const first = app.runBackupNow();
  await app.runBackupNow();
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0], ['POST', '/api/config/backups/run-now']);
  pending.resolve({ ok: true, stdout: 'Synthetic backup created' });
  await first;
  assert.equal(reloaded, 1);
  assert.equal(app.maintenanceOutput, 'Synthetic backup created');
  assert.equal(app.maintBusy, false);
});

test('backup failures release the button and report the failure', async () => {
  const app = createApp();
  const messages = [];
  app.api = async () => { throw new Error('Synthetic network error'); };
  app.showToast = message => messages.push(message);
  await app.runBackupNow();
  assert.equal(app.maintBusy, false);
  assert.match(messages[0], /Synthetic network error/);
});

for (const external of [false, true]) {
  test(`simultaneous shopping submits add one item (${external ? 'external' : 'local'})`, async () => {
    const app = createApp();
    const pending = deferred();
    const writes = [];
    app.cart.external = external;
    app.cart.quickAdd = 'Synthetic flour';
    app.cart.add.name = 'Synthetic flour';
    app.api = async (method, url) => {
      writes.push(url);
      return url.endsWith('consolidate') ? {} : pending.promise;
    };
    app.loadCart = async () => {};
    const first = app.addToCart();
    await app.addToCart();
    assert.equal(writes.length, 1);
    pending.resolve({ ok: true });
    await first;
    assert.equal(app.cart.addBusy, false);
    assert.equal(writes.length, external ? 2 : 1);
  });
}

test('failed shopping submit keeps the draft and permits retry', async () => {
  const app = createApp();
  app.cart.add.name = 'Synthetic flour';
  app.api = async () => { throw new Error('Synthetic failure'); };
  await app.addToCart();
  assert.equal(app.cart.add.name, 'Synthetic flour');
  assert.equal(app.cart.addBusy, false);
});

test('household join cancellation preserves the invitation and makes no request', async () => {
  const app = createApp({ confirm: () => false });
  app.account.joinToken = 'synthetic-invitation';
  app.api = () => assert.fail('No write before household confirmation');
  await app.acceptAccountInvitation();
  assert.equal(app.account.joinToken, 'synthetic-invitation');
  assert.equal(app.account.busy, false);
});

test('confirmed household join posts once and refreshes the household', async () => {
  const calls = [];
  const app = createApp({ confirm: () => true, window: { location: { assign: url => calls.push(url) } } });
  app.session = { loaded: true, role: 'user' };
  app.account.joinToken = 'synthetic-invitation';
  app.api = async (method, url, body) => { calls.push([method, url, body.token]); return {}; };
  app.loadAccount = async () => {};
  await app.acceptAccountInvitation();
  assert.deepEqual(calls[0], ['POST', '/api/account/invitations/accept', 'synthetic-invitation']);
  assert.equal(calls[1], '/account');
  assert.equal(app.account.joinToken, '');
  assert.equal(app.account.busy, false);
});

test('job streams are created only for a loaded admin session', () => {
  let connections = 0;
  class EventSource { constructor() { ++connections; } addEventListener() {} }
  const app = createApp({ EventSource });
  for (const session of [
    { loaded: false, full_access: true },
    { loaded: true, role: 'guest', full_access: false },
    { loaded: true, role: 'user', full_access: false },
  ]) {
    app.session = session;
    app._startEventStream();
    assert.equal(connections, 0);
  }
  app.session = { loaded: true, role: 'admin', full_access: true };
  app._startEventStream();
  app._startEventStream();
  assert.equal(connections, 1);
});

test('a successful cover upload refreshes every width without changing the filename', async () => {
  const app = createApp({ FormData: class { append() {} } });
  app.loadAudit = async () => {};
  app.loadRecipes = async () => {};
  app.fetchWithTimeout = async () => ({ response: { ok: true, status: 200 },
    result: { ok: true, thumbnail: 'thumb.jpg', size_bytes: 1024 } });
  app.api = async () => ({ id: 7, thumb_filename: 'thumb.jpg' });
  await app.openRecipe(7);
  const before = app.recipeThumbnailUrl(app.recipeDetail.data, 400);
  await app.uploadThumbnail(7, { size: 1024 });
  assert.equal(app.recipeDetail.data.thumb_filename, 'thumb.jpg');
  const after = app.recipeThumbnailUrl(app.recipeDetail.data, 400);
  assert.notEqual(after, before);
  assert.match(after, /^\/api\/recipes\/7\/thumb\?w=400&v=/);
  assert.equal(new URL('http://test' + after).searchParams.get('v'),
    new URL('http://test' + app.recipeThumbnailUrl({ id: 7 }, 800)).searchParams.get('v'));
  assert.equal(app.recipeThumbnailUrl({ id: 8 }, 400), '/api/recipes/8/thumb?w=400');
});

test('image backup restore invalidates only the restored recipe cover', async () => {
  const app = createApp({ confirm: () => true });
  app.api = async (method, url) => method === 'POST'
    ? { ok: true } : { id: Number(url.split('/').pop()), thumb_filename: 'thumb.jpg' };
  app.loadRecipes = async () => {};
  await app.openRecipe(7);
  const before = app.recipeThumbnailUrl(app.recipeDetail.data);
  await app.restoreRecipeImageBackup({ id: 55 });
  assert.notEqual(app.recipeThumbnailUrl(app.recipeDetail.data), before);
  assert.equal(app.recipeThumbnailUrl({ id: 8 }), '/api/recipes/8/thumb');
});

test('a late upload cannot overwrite the same recipe opened afresh', async () => {
  const response = deferred();
  const app = createApp({ FormData: class { append() {} } });
  app.api = async () => ({ id: 7, thumb_filename: 'fresh-server-cover.jpg' });
  app.fetchWithTimeout = () => response.promise;
  app.loadAudit = async () => {};
  app.loadRecipes = async () => {};
  await app.openRecipe(7);
  const upload = app.uploadThumbnail(7, { size: 1024 });
  await app.openRecipe(7);
  response.resolve({ response: { ok: true, status: 200 },
    result: { ok: true, thumbnail: 'older-upload.jpg', size_bytes: 1024 } });
  await upload;
  assert.equal(app.recipeDetail.data.thumb_filename, 'fresh-server-cover.jpg');
});

test('admin version restore refreshes the cover but preserves a newer detail context', async () => {
  const response = deferred();
  const app = createApp({ confirm: () => true });
  app.api = async (method, url) => method === 'POST'
    ? response.promise : { id: Number(url.split('/').pop()), thumb_filename: 'thumb.jpg' };
  app.loadAdminVersions = async () => {};
  app.loadRecipes = async () => {};
  await app.openRecipe(7);
  const restoration = app.restoreAdminVersion({ id: 3, recipe_id: 7, version_no: 1 });
  await app.openRecipe(7);
  const epoch = app.recipeDetail.requestEpoch;
  response.resolve({ ok: true, recipe_id: 7, media_restored: true });
  await restoration;
  assert.equal(app.recipeDetail.requestEpoch, epoch);
  assert.match(app.recipeThumbnailUrl(app.recipeDetail.data), /[?&]v=/);
});

function stalledResponse() {
  const body = deferred();
  let signal;
  let active = false;
  let timeout;
  const app = createApp({
    setTimeout(callback) { active = true; timeout = callback; return 1; },
    clearTimeout() { active = false; },
    async fetch(_url, options) {
      signal = options.signal;
      signal.addEventListener('abort', () => {
        const error = new Error('Aborted');
        error.name = 'AbortError';
        body.reject(error);
      }, { once: true });
      return { status: 200, ok: true, json: () => body.promise, blob: () => body.promise };
    },
  });
  return {
    app,
    expire() { if (active) timeout(); else body.resolve({ stale: true }); },
    finish() { body.resolve({ stale: true }); },
    get signal() { return signal; },
    get active() { return active; },
  };
}

test('finishing an old delete cannot close the recipe opened meanwhile', async () => {
  const response = deferred();
  const app = createApp({ confirm: () => true });
  app.api = async (method, url) => method === 'DELETE'
    ? response.promise : { id: Number(url.split('/').pop()), name: 'Rezept' };
  app.loadAudit = async () => {};
  app.loadRecipes = async () => {};
  await app.openRecipe(7);
  const deletion = app.deleteRecipeFromDetail();
  await app.openRecipe(8);
  const epoch = app.recipeDetail.requestEpoch;
  response.resolve({ ok: true });
  await deletion;
  assert.equal(app.recipeDetail.show, true);
  assert.equal(app.recipeDetail.data.id, 8);
  assert.equal(app.recipeDetail.requestEpoch, epoch);
});

for (const changedDetail of ['other-recipe', 'closed']) {
  test(`old image restore preserves detail state after ${changedDetail}`, async () => {
    const response = deferred();
    const app = createApp({ confirm: () => true });
    app.api = async (method, url) => method === 'POST'
      ? response.promise : { id: Number(url.split('/').pop()) };
    app.loadRecipes = async () => {};
    await app.openRecipe(7);
    const restoration = app.restoreRecipeImageBackup({ id: 55, recipe_id: 7 });
    if (changedDetail === 'closed') app.closeRecipeDetail();
    else await app.openRecipe(8);
    const epoch = app.recipeDetail.requestEpoch;
    app.recipeDetail.imageRestoring = 99;
    response.resolve({ ok: true, recipe_id: 7 });
    await restoration;
    assert.equal(app.recipeDetail.requestEpoch, epoch);
    assert.equal(app.recipeDetail.imageRestoring, 99);
    assert.equal(app.recipeDetail.show, changedDetail !== 'closed');
    assert.equal(app.recipeDetail.data?.id, changedDetail === 'closed' ? undefined : 8);
  });
}

test('an old generation response cannot finish a newer recipe generation indicator', async () => {
  const first = deferred(), second = deferred();
  const app = createApp();
  app.session = { loaded: true, role: 'admin', is_admin: true };
  const queued = [];
  app.api = async (method, url) => {
    if (method === 'POST') {
      queued.push(url);
      return url.includes('/7/') ? first.promise : second.promise;
    }
    return { id: Number(url.split('/').pop()) };
  };
  await app.openRecipe(7);
  const oldGeneration = app.generateRecipeImage();
  await app.openRecipe(8);
  const newGeneration = app.generateRecipeImage();
  assert.equal(queued.length, 2, 'Opening another recipe must allow its own image action');
  first.resolve({ ok: true });
  await oldGeneration;
  assert.equal(app.recipeDetail.data.id, 8);
  assert.equal(app.recipeDetail.imageGenerating, true);
  assert.equal(app.recipeDetail.data.image_generation_status, undefined);
  second.resolve({ ok: true });
  await newGeneration;
  assert.equal(app.recipeDetail.imageGenerating, false);
  assert.equal(app.recipeDetail.data.image_generation_status, 'pending');
});

test('JSON deadline includes the response body', async () => {
  const fixture = stalledResponse();
  const request = fixture.app.api('GET', '/slow-body');
  const checked = assert.rejects(request, /Zeitüberschreitung/);
  await flush();
  fixture.expire();
  await checked;
  assert.equal(fixture.active, false);
});

test('PDF deadline includes the response body', async () => {
  const fixture = stalledResponse();
  const checked = assert.rejects(fixture.app.fetchPdf('/slow-pdf'), /Zeitüberschreitung/);
  await flush();
  fixture.expire();
  await checked;
  assert.equal(fixture.active, false);
});

test('caller abort after headers cancels JSON without returning stale data', async () => {
  const fixture = stalledResponse();
  const controller = new AbortController();
  const request = fixture.app.api('GET', '/old-body', undefined, { signal: controller.signal });
  await flush();
  controller.abort();
  fixture.finish();
  assert.equal(await request, null);
  assert.equal(fixture.signal.aborted, true);
  assert.equal(fixture.active, false);
});

test('already aborted caller signal is forwarded', async () => {
  const controller = new AbortController();
  controller.abort();
  let fetched = false;
  const app = createApp({ fetch: async (_url, options) => {
    fetched = true;
    assert.equal(options.signal.aborted, true);
    const error = new Error('Aborted');
    error.name = 'AbortError';
    throw error;
  } });
  assert.equal(await app.api('GET', '/cancelled', undefined, { signal: controller.signal }), null);
  assert.equal(fetched, true);
});

test('a real HTTP response that stalls after its headers times out', { timeout: 5000 }, async t => {
  const server = http.createServer((_request, response) => {
    response.writeHead(200, { 'Content-Type': 'application/json' });
    response.flushHeaders();
    response.write('{"partial":');
  });
  t.signal.addEventListener('abort', () => {
    server.closeAllConnections();
    server.close();
  }, { once: true });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    const app = createApp({ fetch });
    const address = server.address();
    await assert.rejects(
      app.api('GET', `http://127.0.0.1:${address.port}/stalled`, undefined, { timeoutMs: 1000 }),
      /Zeitüberschreitung/,
    );
  } finally {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
});

test('pagination failure retains recipes and retries only when requested', async () => {
  const app = createApp();
  app.recipes.items = [{ id: 1, name: 'First page' }];
  app.recipes.total = 2;
  let calls = 0;
  app.api = async () => { ++calls; throw new Error('Offline'); };
  await app.loadMoreRecipes();
  assert.match(app.recipes.moreError, /nicht.*geladen/);
  assert.equal(app.recipes.items[0].name, 'First page');
  assert.equal(app.recipes.loadingMore, false);
  await app.loadMoreRecipes();
  assert.equal(calls, 1);
  app.api = async () => ({ items: [{ id: 2, name: 'Second page' }], total: 2 });
  await app.loadMoreRecipes({ retry: true });
  assert.equal(app.recipes.moreError, '');
  assert.equal(app.recipes.items.length, 2);
});

test('an older week response cannot replace the latest requested week', async () => {
  const app = createApp();
  const older = deferred(), latest = deferred();
  const calls = [];
  app.api = (_method, url, _body, options) => {
    calls.push(options);
    return url.includes('2026-10-05') ? latest.promise : older.promise;
  };
  const first = app.loadMealPlan('2026-09-28');
  const second = app.loadMealPlan('2026-10-05');
  latest.resolve({ week_start: '2026-10-05', days: [] });
  await second;
  older.resolve({ week_start: '2026-09-28', days: [] });
  await first;
  assert.equal(app.mealPlan.weekStart, '2026-10-05');
  assert.equal(calls[0].signal.aborted, true);
});

test('an obsolete week error cannot stop the current loading indicator', async () => {
  const app = createApp();
  const older = deferred(), latest = deferred();
  app.api = (_method, url) => url.includes('2026-10-05') ? latest.promise : older.promise;
  const first = app.loadMealPlan('2026-09-28');
  const second = app.loadMealPlan('2026-10-05');
  older.reject(new Error('Old failure'));
  await first;
  assert.equal(app.mealPlan.loading, true);
  assert.equal(app.mealPlan.error, '');
  latest.resolve({ week_start: '2026-10-05' });
  await second;
  assert.equal(app.mealPlan.loading, false);
});

test('cart refresh keeps the latest connection mode and items', async () => {
  const app = createApp();
  const older = deferred(), latest = deferred();
  let statusCount = 0;
  app.api = async (_method, url) => {
    if (url.endsWith('/status')) return ++statusCount === 1 ? older.promise : latest.promise;
    if (url === '/api/cart') return { items: [{ name: 'New item' }] };
    return { Old: [{ name: 'Old item' }] };
  };
  const first = app.loadCart(), second = app.loadCart();
  latest.resolve({ configured: false });
  await second;
  older.resolve({ configured: true });
  await first;
  assert.equal(app.cart.external, false);
  assert.equal(app.cart.items[0].name, 'New item');
  assert.equal(app.cart.list.length, 0);
});

test('cart connection failure is recoverable without discarding the displayed list', async () => {
  const app = createApp();
  app.cart.items = [{ name: 'Saved display' }];
  app.api = async () => { throw new Error('Offline'); };
  await app.loadCart();
  assert.match(app.cart.connectionError, /nicht.*erreichbar|nicht.*geladen/);
  assert.equal(app.cart.items[0].name, 'Saved display');
  assert.equal(app.cart.loading, false);
});

test('a refresh started before a saved checkbox cannot undo the saved change', async () => {
  const app = createApp();
  const snapshot = deferred();
  app.cart.items = [{ id: 1, name: 'Milch', checked: false }];
  app.api = async (_method, url) => {
    if (url.endsWith('/status')) return { configured: false };
    if (url === '/api/cart') return snapshot.promise;
    return { ok: true };
  };
  const refresh = app.loadCart();
  await flush();
  await app.toggleCartItem(1, true);
  snapshot.resolve({ items: [{ id: 1, name: 'Milch', checked: false }] });
  await refresh;
  assert.equal(app.cart.items[0].checked, true);
  assert.equal(app.cart.loading, false);
});

test('an old refresh cannot restore an article that was just deleted', async () => {
  const app = createApp();
  const snapshot = deferred();
  app.cart.items = [{ id: 1, name: 'Milch' }];
  app.api = async (_method, url) => {
    if (url.endsWith('/status')) return { configured: false };
    if (url === '/api/cart') return snapshot.promise;
    return { ok: true };
  };
  const refresh = app.loadCart();
  await flush();
  await app.deleteCartItem(1);
  snapshot.resolve({ items: [{ id: 1, name: 'Milch' }] });
  await refresh;
  assert.equal(app.cart.items.length, 0);
});

test('autocomplete keeps newer suggestions when older results finish last', async () => {
  const app = createApp();
  const older = deferred(), latest = deferred();
  app.api = (_method, url) => url.includes('Tom') ? older.promise : latest.promise;
  app.cart.add.name = 'Tom';
  const first = app.loadCartSuggestions();
  app.cart.add.name = 'Milch';
  const second = app.loadCartSuggestions();
  latest.resolve({ items: [{ name: 'Milch' }] });
  await second;
  older.resolve({ items: [{ name: 'Tomaten' }] });
  await first;
  assert.equal(app.cart.suggestions[0].name, 'Milch');
});

test('clearing or choosing an ingredient cancels outstanding suggestions', async () => {
  for (const choose of [false, true]) {
    const app = createApp();
    const pending = deferred();
    app.api = () => pending.promise;
    app.cart.add.name = 'Tom';
    const request = app.loadCartSuggestions();
    if (choose) app.chooseCartSuggestion({ name: 'Tomaten' });
    else { app.cart.add.name = ''; await app.loadCartSuggestions(); }
    pending.resolve({ items: [{ name: 'Old suggestion' }] });
    await request;
    assert.equal(app.cart.suggestions.length, 0);
  }
});

test('obsolete ingredient facets do not replace the current filter choices', async () => {
  const app = createApp();
  const older = deferred(), latest = deferred();
  let count = 0;
  app.api = () => ++count === 1 ? older.promise : latest.promise;
  const first = app.loadFacets(), second = app.loadFacets();
  latest.resolve({ ingredients: [{ canonical_name: 'milch' }] });
  await second;
  older.resolve({ ingredients: [{ canonical_name: 'tomate' }] });
  await first;
  assert.equal(app.recipes.facets.ingredients[0].canonical_name, 'milch');
});

test('ingredient facet failure has no unhandled rejection or data loss', async () => {
  const app = createApp();
  app.recipes.facets = { ingredients: [{ canonical_name: 'milch' }] };
  app.api = async () => { throw new Error('Offline'); };
  await app.loadFacets();
  assert.equal(app.recipes.facets.ingredients[0].canonical_name, 'milch');
});

test('library failure retains displayed recipes and can be retried', async () => {
  const app = createApp();
  app.recipes.items = [{ name: 'Saved display' }];
  app.api = async () => { throw new Error('Offline'); };
  await app.loadRecipes();
  assert.match(app.recipes.error, /nicht.*geladen/);
  assert.equal(app.recipes.items[0].name, 'Saved display');
  app.api = async () => ({ items: [{ name: 'Refreshed' }], total: 1 });
  await app.loadRecipes();
  assert.equal(app.recipes.error, '');
  assert.equal(app.recipes.items[0].name, 'Refreshed');
});

test('navigation closes recipe details through the cleanup path', () => {
  const app = createApp();
  let released = 0;
  const controller = new AbortController();
  app.recipeDetail.show = true;
  app.recipeDetail.cookMode = true;
  app.recipeDetail.wakeLockActive = true;
  app.recipeDetail.controller = controller;
  app._wakeLock = { release: async () => { ++released; } };
  app._stopAdminRuntime = () => {};
  app.loadCart = () => {};
  app.navTo('cart', { updateUrl: false });
  assert.equal(released, 1, 'Navigation must release the screen lock');
  assert.equal(controller.signal.aborted, true, 'Navigation must cancel outstanding detail requests');
  assert.equal(app.recipeDetail.cookMode, false);
  assert.equal(app.recipeDetail.wakeLockActive, false);
  assert.equal(app.recipeDetail.show, false);
  assert.equal(app.page, 'cart');
});

test('guest writes are blocked before fetch while household reads work', async () => {
  let calls = 0;
  const app = createApp({ Headers, fetch: async () => { ++calls; return { ok: true, json: async () => ({ items: [] }) }; } });
  app.session = { loaded: true, role: 'guest' };
  assert.equal(app.canWrite(), false);
  await assert.rejects(app.api('POST', '/api/cart/add', { name: 'Tomaten' }), /Gäste/);
  assert.equal(calls, 0);
  await app.api('GET', '/api/cart');
  assert.equal(calls, 1);
});

test('unknown sessions cannot enable editing after a failed role check', async () => {
  const app = createApp();
  app.session = { loaded: true, role: 'guest' };
  app.api = async () => { throw new Error('Offline'); };
  await app.loadSession();
  assert.equal(app.canWrite(), false);
  app.session = { loaded: true, role: 'user' };
  assert.equal(app.canWrite(), true);
});

test('account invitations retain their link until revoked', async () => {
  const app = createApp({ window: { location: { origin: 'https://rezepte.test' } } });
  app.session = { loaded: true, role: 'user' };
  const calls = [];
  app.api = async (method, path) => {
    calls.push([method, path]);
    return path === '/api/account/invitations' ? { id: 3, invite_path: '/register?invite=demo-token' } : { members: [] };
  };
  await app.createAccountInvitation();
  assert.equal(app.accountInvitationUrl(), 'https://rezepte.test/register?invite=demo-token');
  await app.revokeAccountInvitation({ id: 3 });
  assert.equal(app.account.invitation, null);
  assert.deepEqual(calls.find(([method]) => method === 'DELETE'), ['DELETE', '/api/account/invitations/3']);
  assert.equal(app.account.busy, false);
});

test('accepting an invitation extracts the token and refreshes membership', async () => {
  const app = createApp({ confirm: () => true });
  app.session = { loaded: true, role: 'user' };
  let received;
  app.account.joinToken = 'https://rezepte.test/register?invite=demo-invitation-token';
  app.api = async (method, path, payload) => {
    if (method === 'POST') received = payload.token;
    return { members: [{ username: 'Partner' }] };
  };
  await app.acceptAccountInvitation();
  assert.equal(received, 'demo-invitation-token');
  assert.equal(app.account.data.members[0].username, 'Partner');
  assert.equal(app.account.joinToken, '');
  assert.equal(app.account.busy, false);
});

test('a late account refresh cannot restore revoked invitations or old membership', async () => {
  const app = createApp();
  const first = deferred();
  let calls = 0;
  app.api = () => ++calls === 1 ? first.promise : Promise.resolve({ members: [{ username: 'Current' }], invitations: [] });
  const older = app.loadAccount();
  await app.loadAccount();
  first.resolve({ members: [{ username: 'Old' }], invitations: [{ id: 1 }] });
  await older;
  assert.equal(app.account.data.members[0].username, 'Current');
  assert.equal(app.account.data.invitations.length, 0);
  assert.equal(app.account.loading, false);
});

test('user directory finds provider-only accounts and can clear an active filter', async () => {
  const app = createApp();
  app.session = { loaded: true, is_admin: true, username: 'admin' };
  const users = [
    { id: 1, username: 'admin', auth_methods: ['password'] },
    { id: 2, username: 'relay_random', auth_methods: ['apple'] },
    { id: 3, username: 'second_random', auth_methods: ['google', 'apple'] },
  ];
  app.api = async () => ({ users });
  app.users.search = 'admin';
  await app.loadUsers();
  assert.deepEqual(Array.from(app.filteredUsers(), user => user.id), [1]);
  assert.equal(app.users.items.length, 3);
  app.resetUserSearch();
  assert.deepEqual(Array.from(app.filteredUsers(), user => user.id), [1, 2, 3]);
  app.users.search = '  APPLE  ';
  assert.deepEqual(Array.from(app.filteredUsers(), user => user.id), [2, 3]);
  app.users.search = 'Google';
  assert.deepEqual(Array.from(app.filteredUsers(), user => user.id), [3]);
  app.users.search = 'Passwort';
  assert.deepEqual(Array.from(app.filteredUsers(), user => user.id), [1]);
});

test('user directory tolerates older payloads without claiming a password login', () => {
  const app = createApp();
  app.users.items = [{ id: 1, username: 'legacy' }];
  app.users.search = 'legacy';
  assert.equal(app.filteredUsers().length, 1);
  assert.deepEqual(Array.from(app.userAuthMethods(app.users.items[0])), []);
  assert.deepEqual(Array.from(app.userAuthMethods({ auth_methods: ['apple', 'apple', 'unexpected'] })), ['Apple']);
});

test('normal users cannot call administrative account mutations', async () => {
  const app = createApp({ confirm: () => true });
  app.session = { loaded: true, role: 'user', is_admin: false };
  app.users.draft = { username: 'Other', password: 'valid-password', role: 'admin' };
  app.api = () => { throw new Error('Must not send an admin request'); };
  await app.saveUser();
  await app.deleteUser({ id: 5, username: 'Other' });
  await app.revokeUserSessions({ id: 5, username: 'Other' });
  await app.loadUsers();
  assert.equal(app.users.error, '');
  assert.equal(app.users.draft, null);
});

test('password policy counts Unicode characters and UTF-8 bytes', () => {
  const app = createApp();
  assert.equal(app.accountPasswordError('😀'.repeat(18)), '');
  assert.match(app.accountPasswordError('😀'.repeat(19)), /72 UTF-8/);
  assert.match(app.accountPasswordError('😀'.repeat(9)), /10 Zeichen/);
});

test('password change clears secrets and redirects only after success', async () => {
  const routes = [], calls = [];
  const app = createApp({ window: { location: { assign: value => routes.push(value) } } });
  app.session = { loaded: true, role: 'user' };
  app.account.currentPassword = 'current-private';
  app.account.newPassword = app.account.confirmPassword = 'new-private-password';
  app.api = async (method, path, body) => { calls.push({ method, path, body }); return { ok: true, reauthenticate: true }; };
  await app.changeAccountPassword();
  assert.equal(calls[0].path, '/api/account/password');
  assert.equal(calls[0].body.current_password, 'current-private');
  assert.equal(app.account.currentPassword, '');
  assert.equal(app.account.newPassword, '');
  assert.deepEqual(routes, ['/login?notice=password-changed']);
});

test('household deletion rejection remains visible without reporting success or navigating', async () => {
  const routes = [];
  const app = createApp({ confirm: () => true, window: { location: { assign: value => routes.push(value) } } });
  app.session = { loaded: true, role: 'user' };
  app.account.deletePassword = 'current-private';
  app.api = async () => { throw new Error('Bitte zuerst eine weitere Person einladen.'); };
  await app.deleteAccount();
  assert.match(app.account.error, /weitere Person/);
  assert.equal(app.account.deletePassword, '');
  assert.equal(app.account.notice, '');
  assert.deepEqual(routes, []);
});

test('admin create is single-flight and never sends empty optional password when editing', async () => {
  const app = createApp({ confirm: () => true });
  const calls = [], pending = deferred();
  app.session = { loaded: true, role: 'admin', is_admin: true, username: 'Owner' };
  app.loadUsers = async () => {};
  app.editUser({ id: 4, username: 'Other', role: 'user', disabled: false });
  app.users.draft.disabled = true;
  app.api = (method, path, body) => { calls.push({ method, path, body }); return pending.promise; };
  const first = app.saveUser();
  await app.saveUser();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, 'PATCH');
  assert.equal(calls[0].body.disabled, true);
  assert.equal('password' in calls[0].body, false);
  pending.resolve({ ok: true }); await first;
  assert.equal(app.users.draft, null);
  assert.equal(app.users.notice, 'Benutzer gespeichert.');
});

test('last-admin error keeps the edit form and clears its password', async () => {
  const app = createApp({ confirm: () => true });
  app.session = { loaded: true, role: 'admin', is_admin: true, username: 'Owner' };
  app.editUser({ id: 1, username: 'Owner', role: 'admin' });
  app.users.draft.role = 'user'; app.users.draft.password = 'private-new-password';
  app.api = async () => { throw new Error('Der letzte Administrator muss erhalten bleiben.'); };
  await app.saveUser();
  assert.match(app.users.error, /letzte Administrator/);
  assert.equal(app.users.draft.password, '');
  assert.equal(app.users.draft.id, 1);
});

test('current session revoke redirects while another device revoke refreshes only', async () => {
  const routes = [], paths = [];
  const app = createApp({ confirm: () => true, window: { location: { assign: value => routes.push(value) } } });
  app.session = { loaded: true, role: 'user' };
  app.loadAccount = async () => {};
  app.api = async (method, path) => { paths.push(path); return { ok: true }; };
  await app.revokeAccountSession({ id: 'other', is_current: false });
  assert.deepEqual(routes, []);
  await app.revokeAccountSession({ id: 'current', is_current: true });
  assert.deepEqual(routes, ['/login']);
  assert.deepEqual(paths, ['/api/account/sessions/other', '/api/account/sessions/current']);
});

test('disabled and unknown identity providers never get link controls', () => {
  const app = createApp();
  app.account.providers = [{ id: 'apple', enabled: true }, { id: 'google', enabled: true }];
  app.account.identities = [{ provider: 'apple' }];
  assert.deepEqual(Array.from(app.availableAccountProviders(), provider => provider.id), ['google']);
});

test('normal and unresolved sessions cannot launch AI, import or OCR handlers directly', async () => {
  const blocked = ['saveHouseholdImport', 'importRecipeFile', 'cleanupFailedJobs', 'importUrl', 'bulkSkipPending', 'resolveItem',
    'reanalyzeHistoryOne', 'reanalyzeHistoryAll', 'reanalyzeJunkOnly', 'cleanupAllJunk', 'scanPendingPhoto',
    'reanalyzeOne', 'retryFailed', 'clearAllFailed', 'reanalyzeAll', 'openEditItem', 'saveEditItem', 'deleteItem',
    'generateRecipeImage', 'syncRecipes', 'extractIngredients', 'computeNutrition', 'rescrapeFromDetailModal',
    'loadPdfPages', 'applyPdfPageEdits', 'loadPdfPreflight', 'runAdminPdf', 'runMaintenance', 'startRecipeImageBackfill',
    'retryFailedDownload', 'startAiSanity', 'recoverEmpty', 'rescrapeBulkRecipeIds', 'rescrapeBulkMissingIngredients',
    'rescrapeBulkMissingSteps', 'bulkComputeNutrition', 'rescrapeRecipe', 'rescrapeBulkNoImage', 'extractFrame',
    'bulkExtractFrames', 'applyFinding', 'applyAllFindings', 'runTest', 'testOpenAI'];
  for (const session of [{ loaded: true, role: 'user', is_admin: false }, { loaded: true, role: 'guest', is_admin: false }, { loaded: false, role: 'admin', is_admin: true }]) {
    const app = createApp({ confirm: () => assert.fail('No privileged confirmation'), prompt: () => assert.fail('No privileged prompt'), fetch: () => assert.fail('No privileged fetch') });
    app.session = session;
    app.recipeDetail.data = { id: 7, can_edit: true, ingredients: [{ name: 'flour' }] };
    app.manualImportUrl = 'https://example.test/recipe';
    app.api = () => assert.fail('No privileged API call');
    for (const action of blocked) await app[action]({ id: 7, url: 'https://example.test/recipe' }, 'approve');
    assert.equal(app.canWrite(), session.role === 'user');
  }
});

test('normal account can create its own variant from a read-only global recipe', async () => {
  const app = createApp({ prompt: () => 'Meine Variante' });
  app.session = { loaded: true, role: 'user', is_admin: false };
  const original = { id: 7, name: 'Original', can_edit: false, visibility: 'global' };
  const calls = [];
  app.api = async (method, url, payload) => {
    if (method === 'POST') { calls.push({ url, payload }); return { ok: true, recipe_id: 8 }; }
    return url.endsWith('/7') ? original : { id: 8, name: 'Meine Variante', can_edit: true, visibility: 'private' };
  };
  app.loadRecipes = async () => {};
  await app.openRecipe(7);
  assert.equal(app.canEditRecipe(), false);
  await app.createOwnRecipeVariant();
  assert.equal(calls[0].url, '/api/recipes/7/duplicate');
  assert.equal(calls[0].payload.new_name, 'Meine Variante');
  assert.equal(app.recipeDetail.data.id, 8);
  assert.equal(app.canEditRecipe(), true);
  assert.equal(original.name, 'Original');
});

test('normal account profile never requests its former import workspace', async () => {
  const app = createApp();
  app.session = { loaded: true, role: 'user', is_admin: false };
  const calls = [];
  app.api = async (_, url) => { calls.push(url); return url === '/api/account' ? { is_guest: false } : {}; };
  await app.loadAccount();
  assert.equal(calls.includes('/api/account/imports'), false);
  assert.ok(calls.includes('/api/account/profile'));
  assert.deepEqual(Array.from(app.account.imports), []);
});

test('four roles separate normal editing, import and administration', () => {
  const app = createApp();
  for (const [role, write, importing, admin] of [
    ['guest', false, false, false], ['user', true, false, false],
    ['full_user', true, true, false], ['admin', true, true, true],
  ]) {
    app.session = { loaded: true, role, is_admin: admin };
    assert.equal(app.canWrite(), write);
    assert.equal(app.canImport(), importing);
    assert.equal(app.canUseAdminTools(), admin);
  }
  app.session = { loaded: false, role: 'full_user' };
  assert.equal(app.canImport(), false);
});

test('full users import privately even with stale global visibility and cannot run maintenance', async () => {
  const confirmations = [];
  const app = createApp({ confirm: message => { confirmations.push(message); return true; } });
  app.session = { loaded: true, role: 'full_user', is_admin: false };
  app.manualImportUrl = 'https://recipes.example/meal';
  app.manualImportVisibility = 'global';
  const calls = [];
  app.api = async (method, path, body) => { calls.push({ method, path, body }); return { ok: true }; };
  app.loadRecipes = async () => {};
  await app.importUrl();
  assert.equal(calls[0].path, '/api/pending/import-url');
  assert.equal(calls[0].body.visibility, 'private');
  await app.runAdminPdf();
  await app.runMaintenance();
  await app.bulkSkipPending();
  assert.equal(calls.length, 1);
  assert.equal(confirmations.length, 1, 'Only the allowed import asks for consent');
  assert.match(confirmations[0], /OpenAI/);
  assert.equal(calls[0].body.ai_processing_consent, 'openai-recipe-v1');
  assert.equal(app.runScraper, undefined);
  assert.equal(app.testMail, undefined);
  assert.equal(app.saveSchedule, undefined);
});

test('named guests can revoke their own session but cannot change household data', async () => {
  const calls = [];
  const app = createApp({ fetch: async (url) => { calls.push(url); return { ok: true, json: async () => ({ ok: true }) }; } });
  app.session = { loaded: true, role: 'guest' };
  app.account.profile = { id: 5, role: 'guest' };
  await app.api('DELETE', '/api/account/sessions/current');
  await assert.rejects(app.api('POST', '/api/account/invitations', {}), /Gäste/);
  await assert.rejects(app.api('POST', '/api/pending/import-url', {}), /Gäste/);
  assert.deepEqual(calls, ['/api/account/sessions/current']);
});

test('variant cancellation and server errors keep the original recipe unchanged', async () => {
  for (const status of [403, 409]) {
    const app = createApp({ prompt: () => 'Variante' });
    app.session = { loaded: true, role: 'user', is_admin: false };
    const original = { id: 7, name: 'Original', can_edit: false };
    app.api = async method => { if (method === 'POST') throw Object.assign(new Error(`Serverfehler ${status}`), { status }); return original; };
    let message = '';
    app.showToast = value => { message = value; };
    await app.openRecipe(7); await app.createOwnRecipeVariant();
    assert.match(message, new RegExp(String(status)));
    assert.equal(app.recipeDetail.data, original);
    assert.equal(app.recipeDetail.duplicating, false);
  }
  const cancelled = createApp({ prompt: () => null });
  cancelled.session = { loaded: true, role: 'user' };
  cancelled.recipeDetail.data = { id: 7 };
  cancelled.api = () => assert.fail('Cancelled variant must not request');
  await cancelled.createOwnRecipeVariant();
});
