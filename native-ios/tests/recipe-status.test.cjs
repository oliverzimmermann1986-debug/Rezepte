const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const ts = require('typescript');

const source = fs.readFileSync(path.join(__dirname, '../src/lib/recipe-status.ts'), 'utf8');
const moduleValue = { exports: {} };
vm.runInNewContext(ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText, { module: moduleValue, exports: moduleValue.exports });
const nativeStatus = moduleValue.exports.recipeStatus;
const web = { window: {} };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../app/static/features/recipes.js'), 'utf8'), web);
const webStatus = web.window.RezepteFeatures.recipes().recipeStatus;

for (const [name, recipe, label, tone] of [
  ['waiting', { ingredients_status: 'pending' }, '⏳ Zutaten werden ermittelt', 'warning'],
  ['running with stale contents', { ingredients_status: 'running' }, '⏳ Zutaten werden ermittelt', 'warning'],
  ['failed with stale contents', { ingredients_status: 'error' }, '⚠ Auswertung fehlgeschlagen', 'danger'],
  ['missing source text', { ingredients_status: 'skipped' }, '⚠ Beschreibung fehlt', 'warning'],
  ['manual review', { needs_manual_care: true }, '⚠ Manuell pflegen', 'warning'],
  ['missing ingredients despite ok status', { ingredients_count: 0 }, '⚠ Manuell pflegen', 'warning'],
  ['missing steps despite ok status', { steps_count: 0 }, '⚠ Manuell pflegen', 'warning'],
  ['complete successful recipe', {}, '✓ Kochfertig', 'success'],
]) {
  test(`web and native recipe status: ${name}`, () => {
    const fixture = { ingredients_status: 'ok', ingredients_count: 3, steps_count: 2, needs_manual_care: false, ...recipe };
    for (const status of [nativeStatus(fixture), webStatus(fixture)]) {
      assert.equal(status.label, label);
      assert.equal(status.tone, tone);
    }
  });
}

test('semantic status text has at least 4.5:1 contrast on both light surfaces', () => {
  const css = fs.readFileSync(path.join(__dirname, '../../app/static/rezepte.css'), 'utf8');
  function luminance(hex) {
    const rgb = hex.match(/\w\w/g).map(v => parseInt(v, 16) / 255)
      .map(v => v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
    return rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722;
  }
  for (const variable of ['ok', 'warn', 'err', 'pending']) {
    const foreground = luminance(css.match(new RegExp(`--${variable}: #([a-f0-9]{6})`))[1]);
    for (const surface of ['fffaf0', 'fffdf8']) {
      const background = luminance(surface);
      assert.ok((background + 0.05) / (foreground + 0.05) >= 4.5, `${variable} on ${surface}`);
    }
  }
});
