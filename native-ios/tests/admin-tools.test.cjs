const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const ts = require('typescript');

function harness(file, exported, options = {}) {
  const hooks = [], effects = [], calls = [], alerts = [], providerCalls = [];
  let index = 0, admin = options.admin ?? false, resets = 0, pickerCalls = 0;
  const react = {
    createElement: (type, props, ...children) => ({ type, props: { ...props, children } }), Fragment: 'Fragment',
    useState: initial => { const key = index++; if (!(key in hooks)) hooks[key] = initial; return [hooks[key], value => { hooks[key] = typeof value === 'function' ? value(hooks[key]) : value; }]; },
    useRef: initial => { const key = index++; if (!(key in hooks)) hooks[key] = { current: initial }; return hooks[key]; },
    useEffect: (fn, deps) => { const key = index++; if (!hooks[key] || deps.some((value, i) => value !== hooks[key][i])) { hooks[key] = deps; effects.push(fn); } },
    useCallback: fn => fn, useMemo: fn => fn(),
  };
  const native = { StyleSheet: { create: value => value }, Alert: { alert: (...args) => alerts.push(args) },
    Platform: { OS: 'ios' }, useWindowDimensions: () => ({ width: 390, fontScale: 1 }) };
  for (const name of ['Text', 'View', 'TextInput', 'Pressable', 'ScrollView', 'Modal', 'FlatList', 'KeyboardAvoidingView', 'ActivityIndicator']) native[name] = name;
  const auth = () => ({ username: 'Example', isAdmin: admin, isGuest: options.guest ?? false, ready: true, token: 'synthetic', serverUrl: 'https://example.invalid',
    loadProviders: async () => options.providers || [], signInWithProvider: async (...args) => providerCalls.push(args) });
  const api = async (url, init = {}) => { calls.push({ url, init }); return options.request ? options.request(url, init) : { ok: true }; };
  const deps = {
    react, 'react-native': native, 'react-native-safe-area-context': { SafeAreaView: 'SafeAreaView' },
    '@react-navigation/native': { useFocusEffect: fn => { if (options.runFocus) effects.push(fn); } },
    'expo-image': { Image: 'Image' }, 'expo-symbols': { SymbolView: 'SymbolView' }, 'expo-sharing': {},
    'expo-router': { useRouter: () => ({ replace: () => {} }), useRootNavigationState: () => ({ key: 'root' }), useLocalSearchParams: () => ({}) },
    'expo-share-intent': { useShareIntentContext: () => ({ hasShareIntent: true, isReady: true, shareIntent: {}, resetShareIntent: () => { resets++; } }) },
    'expo-document-picker': { getDocumentAsync: async () => { pickerCalls++; return { canceled: true }; } },
    '@/lib/auth-context': { useAuth: auth },
    '@/lib/api': { api, ApiError: Error, absoluteApiUrl: value => value, apiAuthHeaders: () => ({}), createClientRequestId: () => 'synthetic', deleteCachedFile: async () => {}, uploadFile: api,
      currentApiSessionEpoch: () => 1, isApiSessionEpochCurrent: () => true },
    '@/lib/cache': { invalidateApiCache: async () => {}, invalidateApiCacheByPrefix: async () => {} },
    '@/lib/external-links': { normalizedExternalUrl: () => null, openExternalUrl: async () => {} },
    '@/lib/shared-link': { socialLinkFromShareIntent: () => 'https://example.invalid/recipe' },
    '@/lib/image-picker': { pickEditedJpeg: async () => { pickerCalls++; return options.pick ? options.pick() : null; } },
    '@/components/ui': { PrimaryButton: 'Button', StateView: 'StateView', Screen: 'Screen', sharedStyles: {} },
  };
  function load(relative) {
    const filename = path.resolve(__dirname, '../src', relative);
    const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, esModuleInterop: true, jsx: ts.JsxEmit.React } }).outputText;
    const module = { exports: {} };
    vm.runInNewContext(compiled, { module, exports: module.exports, AbortController, URL, Error, console,
      setTimeout: fn => { effects.push(fn); return 1; }, clearTimeout: () => {},
      require: name => {
        if (name in deps) return deps[name];
        if (name.startsWith('./')) return load(path.join(path.dirname(relative), name + '.ts'));
        if (name.startsWith('@/components/')) return new Proxy({}, { get: (_, key) => String(key) });
        throw new Error(`Unmocked dependency: ${name}`);
      } });
    return module.exports;
  }
  for (const name of ['constants/design', 'lib/editor-rows', 'lib/numbers', 'lib/units', 'lib/account-management']) deps['@/' + name] = load(name + '.ts');
  const Component = load(file)[exported];
  const render = () => { index = 0; return Component({ visible: true, onClose: () => {}, onApplied: () => {}, onChanged: () => {}, onSaved: () => {}, item: { url: 'https://example.invalid/recipe', ai_suggestion: {} } }); };
  function elements(tree) {
    if (!tree || typeof tree !== 'object') return [];
    if (Array.isArray(tree)) return tree.flatMap(elements);
    return [tree, ...elements(tree.props?.children), ...elements(tree.props?.ListHeaderComponent), ...elements(tree.props?.ListFooterComponent)];
  }
  const button = label => { const element = elements(render()).find(item => item.props.label === label); assert.ok(element, label); return element.props; };
  return { render, button, calls, alerts, providerCalls, setAdmin: value => { admin = value; render(); }, elements,
    runEffects: () => { while (effects.length) effects.shift()(); }, resets: () => resets, pickerCalls: () => pickerCalls };
}
async function flush() { for (let i = 0; i < 20; i++) await Promise.resolve(); }

for (const [file, component] of [['shopping-ai-optimizer', 'ShoppingAiOptimizer'], ['admin-ai-sort', 'AdminAiSort'], ['pending-editor', 'PendingEditor']]) {
  test(`normal users cannot render ${component} or start its effects`, () => {
    const h = harness(`components/${file}.tsx`, component);
    assert.equal(h.render(), null); h.runEffects();
    assert.equal(h.calls.length, 0); assert.equal(h.pickerCalls(), 0);
  });
}

test('normal shared links are consumed without import requests', async () => {
  const h = harness('components/shared-link-receiver.tsx', 'SharedLinkReceiver');
  h.render(); h.runEffects(); await flush();
  assert.equal(h.calls.length, 0); assert.equal(h.resets(), 1);
  assert.match(h.alerts[0][0], /Administrator/);
});

test('administrator shared link still reaches the import API', async () => {
  const h = harness('components/shared-link-receiver.tsx', 'SharedLinkReceiver', { admin: true });
  h.render(); h.runEffects(); await flush();
  assert.equal(h.calls[0].url, '/api/pending/import-url');
});

test('normal account screen neither shows import controls nor loads import endpoints', async () => {
  const h = harness('app/(tabs)/account.tsx', 'default', { runFocus: true, request: async () => ({ is_guest: false, members: [], invitations: [] }) });
  h.render(); h.runEffects(); await flush();
  assert.deepEqual(h.calls.map(call => call.url), ['/api/account']);
  assert.equal(h.elements(h.render()).some(item => item.props.label === 'In Sammlung übernehmen' || item.props.label === 'Rezept übernehmen'), false);
});

test('captured account import cannot run after administrator rights are removed', async () => {
  const h = harness('app/(tabs)/account.tsx', 'default', { admin: true });
  h.elements(h.render()).find(item => item.props.accessibilityLabel === 'Rezeptlink').props.onChangeText('https://example.invalid/recipe');
  const importAction = h.button('In Sammlung übernehmen').onPress;
  h.setAdmin(false); importAction(); await flush();
  assert.equal(h.calls.length, 0);
});

test('captured AI action cannot start after administrator rights are removed', async () => {
  const h = harness('components/shopping-ai-optimizer.tsx', 'ShoppingAiOptimizer', { admin: true });
  const action = h.button('Vorschau erstellen').onPress;
  h.setAdmin(false); await action();
  assert.equal(h.calls.length, 0);
});

test('photo selection rechecks administrator rights before uploading or OCR', async () => {
  let resolve;
  const picked = new Promise(done => { resolve = done; });
  const h = harness('components/pending-editor.tsx', 'PendingEditor', { admin: true, pick: () => picked });
  h.button('Foto hinzufügen und scannen').onPress();
  assert.equal(h.pickerCalls(), 1);
  h.setAdmin(false); resolve({ uri: 'file:///synthetic.jpg' }); await flush();
  assert.equal(h.calls.length, 0);
});

test('already displayed AI confirmation cannot apply after administrator removal', async () => {
  const h = harness('components/shopping-ai-optimizer.tsx', 'ShoppingAiOptimizer', { admin: true, request: async () => ({ preview_id: 'synthetic', items: [], summary: { optimized_count: 0 } }) });
  await h.button('Vorschau erstellen').onPress();
  h.button('Optimierung übernehmen').onPress();
  h.setAdmin(false);
  h.alerts[0][2].find(button => button.text === 'Übernehmen').onPress(); await flush();
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].url, '/api/cart/optimize/preview');
});

test('configured provider buttons work for both login and registration', async () => {
  const h = harness('app/login.tsx', 'default', { providers: [{ id: 'google', name: 'Google', enabled: true }] });
  h.render(); h.runEffects(); await flush();
  await h.button('Mit Google fortfahren').onPress(); await flush();
  h.button('Konto erstellen').onPress();
  await h.button('Mit Google fortfahren').onPress(); await flush();
  assert.equal(h.providerCalls.length, 2);
  assert.ok(h.providerCalls.every(call => call[1] === 'google'));
});

test('unconfigured provider discovery shows no pretend provider buttons', async () => {
  const h = harness('app/login.tsx', 'default');
  h.render(); h.runEffects(); await flush();
  assert.equal(h.elements(h.render()).filter(item => /^Mit (Apple|Google) fortfahren$/.test(item.props.label || '')).length, 0);
});
