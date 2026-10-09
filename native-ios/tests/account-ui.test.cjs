const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const ts = require('typescript');

// Execute the shipped component handlers at the native render boundary. These
// tests cover control state and requests; they do not claim a device rendering.
function harness(component, options = {}) {
  const hooks = [], calls = [], logouts = [], dialogs = [];
  let index = 0;
  const react = {
    createElement: (type, props, ...children) => ({ type, props: { ...props, children } }), Fragment: 'Fragment',
    useState: initial => {
      const key = index++; if (!(key in hooks)) hooks[key] = initial;
      return [hooks[key], value => { hooks[key] = typeof value === 'function' ? value(hooks[key]) : value; }];
    },
    useRef: initial => { const key = index++; if (!(key in hooks)) hooks[key] = { current: initial }; return hooks[key]; },
    useCallback: fn => fn, useEffect: () => {},
  };
  const deps = {
    react, '@react-navigation/native': { useFocusEffect: () => {} },
    'react-native': { Text: 'Text', View: 'View', TextInput: 'TextInput', Switch: 'Switch', Modal: 'Modal', ScrollView: 'ScrollView',
      StyleSheet: { create: value => value }, Alert: { alert: (...args) => dialogs.push(args) } },
    'react-native-safe-area-context': { SafeAreaView: 'SafeAreaView' },
    '@/components/ui': { PrimaryButton: 'Button' },
    '@/lib/auth-context': { useAuth: () => ({ isAdmin: options.admin !== false, username: 'Owner', signOut: async value => logouts.push(value), linkProvider: async () => {} }) },
    '@/lib/api': { api: async (url, init = {}) => {
      calls.push({ url, method: init.method || 'GET', body: init.body && JSON.parse(init.body) });
      if (options.request) return options.request(url, init);
      if (url === '/api/account/profile') return { id: 1, username: 'Owner', role: 'admin', password_enabled: true, created_at: 1 };
      if (url === '/api/account/sessions') return { sessions: [] };
      if (url === '/api/account/identities') return { identities: [], providers: [] };
      if (url === '/api/users' && !init.method) return { users: [] };
      return { ok: true };
    } },
  };
  function load(relative) {
    const compiled = ts.transpileModule(fs.readFileSync(path.resolve(__dirname, '../src', relative), 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, esModuleInterop: true, jsx: ts.JsxEmit.React },
    }).outputText;
    const module = { exports: {} };
    vm.runInNewContext(compiled, { module, exports: module.exports, AbortController, Error,
      require: name => { if (!(name in deps)) throw new Error(name); return deps[name]; } });
    return module.exports;
  }
  deps['@/constants/design'] = load('constants/design.ts');
  deps['@/lib/account-management'] = load('lib/account-management.ts');
  const Component = load(`components/${component === 'AdminUsers' ? 'admin-users' : 'account-security'}.tsx`)[component];
  const render = () => { index = 0; return Component({ visible: true, onClose: () => {} }); };
  const elements = tree => !tree || typeof tree !== 'object' ? [] : Array.isArray(tree) ? tree.flatMap(elements) : [tree, ...elements(tree.props?.children)];
  const button = label => { const item = elements(render()).find(item => item.type === 'Button' && item.props.label === label); assert.ok(item, label); return item.props; };
  const field = label => { const item = elements(render()).find(item => item.props.accessibilityLabel === label); assert.ok(item, label); return item.props; };
  const text = () => elements(render()).filter(item => item.type === 'Text').flatMap(item => item.props.children).join(' ');
  return { render, button, field, text, calls, logouts, dialogs };
}
async function flush() { for (let i = 0; i < 20; i++) await Promise.resolve(); }

test('native user management renders no controls for a normal user', () => {
  const h = harness('AdminUsers', { admin: false });
  assert.equal(h.render(), null);
  assert.equal(h.calls.length, 0);
});

test('native admin creates a normal user with explicit credentials and clears the draft', async () => {
  const h = harness('AdminUsers');
  h.button('Benutzer erstellen').onPress();
  h.field('Benutzername').onChangeText('NewUser');
  h.field('Passwort').onChangeText('synthetic-password');
  h.button('Speichern').onPress(); await flush();
  const request = h.calls.find(call => call.method === 'POST');
  assert.equal(request.url, '/api/users');
  assert.equal(request.body.username, 'NewUser');
  assert.equal(request.body.role, 'user');
  assert.equal(request.body.password, 'synthetic-password');
  assert.match(h.text(), /Benutzer erstellt/);
});

test('native password change clears secrets and uses local cleanup after server revocation', async () => {
  const h = harness('AccountSecurity');
  h.button('Kontodaten aktualisieren').onPress(); await flush();
  h.button('+ Passwort ändern oder einrichten').onPress();
  h.field('Aktuelles Passwort').onChangeText('synthetic-current');
  h.field('Neues Passwort').onChangeText('synthetic-new-password');
  h.field('Neues Passwort wiederholen').onChangeText('synthetic-new-password');
  h.button('Passwort speichern & neu anmelden').onPress(); await flush();
  const request = h.calls.find(call => call.method === 'POST');
  assert.equal(request.url, '/api/account/password');
  assert.equal(request.body.current_password, 'synthetic-current');
  assert.equal(h.logouts[0].localOnly, true);
  assert.equal(h.field('Aktuelles Passwort').value, '');
  assert.equal(h.field('Neues Passwort').value, '');
});

test('native account deletion requires confirmation and retains server guard errors', async () => {
  const h = harness('AccountSecurity', { request: async (url, init) => {
    if (init.method === 'DELETE') throw new Error('Bitte zuerst eine weitere Person einladen.');
    if (url === '/api/account/profile') return { username: 'Owner', password_enabled: true };
    if (url === '/api/account/sessions') return { sessions: [] };
    return { identities: [], providers: [] };
  } });
  h.button('Kontodaten aktualisieren').onPress(); await flush();
  h.button('+ Konto löschen').onPress();
  h.field('Aktuelles Passwort').onChangeText('synthetic-current');
  h.button('Mein Konto endgültig löschen').onPress();
  assert.equal(h.calls.filter(call => call.method === 'DELETE').length, 0);
  h.dialogs[0][2].find(button => button.style === 'destructive').onPress(); await flush();
  assert.match(h.text(), /weitere Person einladen/);
  assert.equal(h.logouts.length, 0);
  assert.equal(h.field('Aktuelles Passwort').value, '');
});


for (const [role, label] of [['guest', 'Gast'], ['user', 'Benutzer'], ['full_user', 'Vollbenutzer'], ['admin', 'Admin']]) {
  test(`admin can explicitly assign the ${role} role`, async () => {
    const h = harness('AdminUsers');
    h.button('Benutzer erstellen').onPress();
    h.field('Benutzername').onChangeText('NewUser');
    h.field('Passwort').onChangeText('synthetic-password');
    if (role !== 'user') h.button(label).onPress();
    h.button('Speichern').onPress(); await flush();
    assert.equal(h.calls.find(call => call.method === 'POST').body.role, role);
  });
}
