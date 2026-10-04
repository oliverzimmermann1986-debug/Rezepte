const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const ts = require('typescript');

// Render the shipped TSX at the native boundary, without claiming a device run.
function harness({ fontScale = 1, focused = true, recipe = null, loading = false } = {}) {
  const wakeLocks = [];
  let stateIndex = 0;
  const createElement = (type, props, ...children) => ({ type, props: { ...props, children } });
  const react = {
    createElement, Fragment: 'Fragment',
    useEffect: () => {}, useRef: value => ({ current: value }),
    useState: initial => {
      const index = stateIndex++;
      return [index === 0 ? recipe : index === 3 ? 2 : index === 5 ? loading : initial, () => {}];
    },
  };
  const native = {
    View: 'View', Text: 'Text', Pressable: 'Pressable', ScrollView: 'ScrollView',
    ActivityIndicator: 'ActivityIndicator', Alert: { alert: () => {} },
    StyleSheet: { create: value => value }, useWindowDimensions: () => ({ fontScale }),
  };
  const modules = {
    react, 'react-native': native,
    'react-native-safe-area-context': { SafeAreaView: 'SafeAreaView' },
    'expo-image': { Image: 'Image' }, 'expo-symbols': { SymbolView: 'SymbolView' },
    'expo-router': { Stack: { Screen: 'StackScreen' }, router: { push: () => {} },
      useRouter: () => ({ back: () => {} }), useLocalSearchParams: () => ({ id: '1' }) },
    'expo-keep-awake': { useKeepAwake: (...args) => wakeLocks.push(args) },
    '@react-navigation/native': { useIsFocused: () => focused },
    '@/lib/api': { absoluteApiUrl: value => value, apiAuthHeaders: () => ({}), createClientRequestId: () => 'test-request' },
    '@/lib/auth-context': { useAuth: () => ({ username: 'cook', isGuest: false }) },
    '@/lib/cache': {},
    '@/components/serving-selector': { ServingSelector: 'ServingSelector' },
    '@/components/step-timer': { StepTimer: 'StepTimer' },
  };
  function load(relative) {
    const filename = path.resolve(__dirname, '../src', relative);
    const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, esModuleInterop: true, jsx: ts.JsxEmit.React },
    }).outputText;
    const module = { exports: {} };
    vm.runInNewContext(compiled, { module, exports: module.exports, AbortController, Promise,
      require: name => {
        if (modules[name]) return modules[name];
        throw new Error(`Missing native boundary: ${name}`);
      },
    }, { filename });
    return module.exports;
  }
  modules['@/constants/design'] = load('constants/design.ts');
  modules['@/lib/recipe-status'] = load('lib/recipe-status.ts');
  modules['@/lib/servings'] = load('lib/servings.ts');
  modules['@/components/ui'] = load('components/ui.tsx');
  return { load, wakeLocks };
}

function elements(tree) {
  if (!tree || typeof tree !== 'object') return [];
  if (Array.isArray(tree)) return tree.flatMap(elements);
  return [tree, ...elements(tree.props?.children)];
}

test('recipe button announces name, extraction status, rating and verification', () => {
  const { load } = harness();
  const recipe = { id: 1, name: 'Kartoffelpfanne', ingredients_status: 'error', rating: 4, user_verified: true };
  const tree = load('components/recipe-card.tsx').RecipeCard({ recipe });
  const button = elements(tree).find(item => item.type === 'Pressable');
  assert.match(button.props.accessibilityLabel, /Kartoffelpfanne/);
  assert.match(button.props.accessibilityLabel, /4 von 5 Sternen/);
  assert.match(button.props.accessibilityLabel, /Geprüft/);
  assert.match(button.props.accessibilityLabel, /Fehler|fehlgeschlagen/i);
  assert.equal(elements(tree).find(item => item.type === 'Image').props.accessible, false);
});

test('large Dynamic Type does not truncate the recipe name or description', () => {
  const { load } = harness({ fontScale: 1.8 });
  const recipe = { id: 1, name: 'Ein langer Rezeptname', description: 'Eine lange Beschreibung', ingredients_status: 'pending' };
  const texts = elements(load('components/recipe-card.tsx').RecipeCard({ recipe })).filter(item => item.type === 'Text');
  for (const value of [recipe.name, recipe.description]) {
    assert.equal(texts.find(item => item.props.children.includes(value)).props.numberOfLines, undefined);
  }
});

test('loading and disabled actions expose their native accessibility state', () => {
  const { load } = harness();
  const { StateView, PrimaryButton } = load('components/ui.tsx');
  const state = StateView({ title: 'Rezepte werden geladen', loading: true });
  assert.equal(state.props.accessibilityState.busy, true);
  assert.equal(elements(state).find(item => item.type === 'Text').props.accessibilityRole, 'header');
  assert.equal(PrimaryButton({ label: 'Speichern', disabled: true }).props.accessibilityState.disabled, true);
});

test('a recipe without steps renders an actionable state and acquires no wake lock', () => {
  const h = harness({ recipe: { id: 1, name: 'Leer', steps: [], servings: 2 } });
  const tree = h.load('app/cook/[id].tsx').default();
  assert.ok(elements(tree).some(item => item.props.title === 'Zubereitungsschritte fehlen' && item.props.action === 'Zurück zum Rezept'));
  assert.equal(h.wakeLocks.length, 0);
});

test('only the focused cooking screen mounts the lifetime-bound wake lock', () => {
  const recipe = { id: 1, name: 'Pfanne', servings: 2, steps: [{ instruction: 'Kochen' }], ingredients: [] };
  for (const focused of [true, false]) {
    const h = harness({ focused, recipe });
    const tree = h.load('app/cook/[id].tsx').default();
    for (const element of elements(tree).filter(item => typeof item.type === 'function' && item.type.name === 'CookingWakeLock')) {
      element.type(element.props);
    }
    assert.equal(h.wakeLocks.length, focused ? 1 : 0);
    if (focused) assert.equal(h.wakeLocks[0][0], 'recipe-cooking');
  }
});
