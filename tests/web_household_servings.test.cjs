const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function fixture() {
  const root = path.resolve(__dirname, '../app/static');
  const context = vm.createContext({ window: {}, navigator: {}, console, AbortController,
    URLSearchParams, URL, TextEncoder, setTimeout, clearTimeout });
  for (const name of fs.readdirSync(path.join(root, 'features'))) {
    if (name.endsWith('.js')) vm.runInContext(fs.readFileSync(path.join(root, 'features', name), 'utf8'), context);
  }
  vm.runInContext(fs.readFileSync(path.join(root, 'app.js'), 'utf8'), context);
  const app = context.scrapperApp();
  app.session = { loaded: true, role: 'user', default_servings: 3 };
  app.account.data = { is_owner: false, default_servings: 3 };
  return app;
}

test('a shared member saves only the household portion preference', async () => {
  const app = fixture(); app.account.defaultServings = 6;
  app.api = async (method, route, body) => {
    assert.equal(method, 'PATCH'); assert.equal(route, '/api/account/preferences');
    assert.deepEqual(JSON.parse(JSON.stringify(body)), { default_servings: 6 });
    return { ok: true, household_id: 8, default_servings: 6 };
  };
  await app.saveHouseholdDefaultServings();
  assert.equal(app.session.default_servings, 6);
  assert.equal(app.account.data.default_servings, 6);
  assert.match(app.account.notice, /gespeichert/);
});

test('invalid and rejected preferences do not replace the confirmed default', async () => {
  const app = fixture(); app.account.defaultServings = 25;
  app.api = () => assert.fail('Invalid value must not be sent');
  await app.saveHouseholdDefaultServings();
  assert.equal(app.session.default_servings, 3);
  app.account.defaultServings = 4;
  app.api = async () => { throw new Error('Offline'); };
  await app.saveHouseholdDefaultServings();
  assert.equal(app.session.default_servings, 3); assert.equal(app.account.error, 'Offline');
});

test('new meal uses the household default and recipe changes retain manual portions', async () => {
  const app = fixture(); app.session.default_servings = 4;
  app.mealPlan.optionsLoaded = true;
  app.mealPlan.recipeOptions = [{ id: 42, name: 'Pasta', servings: 2 }];
  app.$nextTick = action => action(); app.$refs = {};
  await app.openMealPlanAdd({ date: '2026-10-12' });
  assert.equal(app.mealPlan.draft.planned_servings, 4);
  app.mealPlan.draft.recipe_id = 42; app.mealPlan.draft.planned_servings = 6;
  app.mealPlanRecipeChanged();
  assert.equal(app.mealPlan.draft.planned_servings, 6);
});

test('read-only users cannot save shared preferences', async () => {
  const app = fixture(); app.session.role = 'guest'; app.account.defaultServings = 6;
  app.api = () => assert.fail('Guest must not write');
  await app.saveHouseholdDefaultServings();
  assert.equal(app.session.default_servings, 3);
});
