const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function appWithConfirmation(confirmation = true) {
  const routes = [];
  const root = path.resolve(__dirname, '../app/static');
  const context = vm.createContext({
    window: { location: { assign: value => routes.push(value) } }, navigator: {}, console,
    AbortController, URLSearchParams, URL, TextEncoder, setTimeout, clearTimeout,
    confirm: () => confirmation,
  });
  for (const name of fs.readdirSync(path.join(root, 'features'))) {
    if (name.endsWith('.js')) vm.runInContext(fs.readFileSync(path.join(root, 'features', name), 'utf8'), context);
  }
  vm.runInContext(fs.readFileSync(path.join(root, 'app.js'), 'utf8'), context);
  const app = context.scrapperApp();
  app.session = { loaded: true, role: 'user' };
  app.account.deleteHousehold = true;
  app.account.deletePassword = 'synthetic-password';
  return { app, routes };
}

test('household erasure requires exact phrase and a separate final confirmation', async () => {
  for (const accepted of [false, true]) {
    const { app, routes } = appWithConfirmation(accepted);
    app.api = () => assert.fail('Unconfirmed erasure must not call the server');
    app.account.deleteConfirmation = accepted ? 'haushalt löschen' : 'HAUSHALT LÖSCHEN';
    await app.deleteAccount();
    assert.deepEqual(routes, []);
  }
});

test('confirmed household erasure submits no client-selected household ID', async () => {
  const { app, routes } = appWithConfirmation();
  app.account.deleteConfirmation = 'HAUSHALT LÖSCHEN';
  let body;
  app.api = async (method, route, payload) => {
    assert.equal(method, 'DELETE');
    assert.equal(route, '/api/account/profile');
    body = JSON.parse(JSON.stringify(payload));
    return { ok: true, status: 'deleted' };
  };
  await app.deleteAccount();
  assert.deepEqual(body, { current_password: 'synthetic-password', delete_household: true, confirmation: 'HAUSHALT LÖSCHEN' });
  assert.deepEqual(routes, ['/login']);
  assert.equal(app.account.deletePassword, '');
});

test('pending physical erasure stays visible without claiming completed deletion', async () => {
  const { app, routes } = appWithConfirmation();
  app.account.deleteConfirmation = 'HAUSHALT LÖSCHEN';
  app.api = async () => ({ ok: true, status: 'deletion_pending', message: 'Dateibereinigung wird fortgesetzt.' });
  await app.deleteAccount();
  assert.equal(app.account.deletionAccepted, true);
  assert.equal(app.account.notice, 'Dateibereinigung wird fortgesetzt.');
  assert.equal(app.account.deletePassword, '');
  assert.deepEqual(routes, []);
});

test('ordinary account deletion preserves shared-household request semantics', async () => {
  const { app } = appWithConfirmation();
  app.account.deleteHousehold = false;
  app.account.deleteConfirmation = 'HAUSHALT LÖSCHEN';
  let body;
  app.api = async (_, __, payload) => { body = payload; return { ok: true }; };
  await app.deleteAccount();
  assert.equal(body.delete_household, false);
  assert.equal(body.confirmation, '');
});
