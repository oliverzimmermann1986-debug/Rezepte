// Exercise the real user-action handlers with synthetic data, never a server.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../app/static');
const consent = 'openai-recipe-v1';

function createApp(allow) {
  const prompts = [], requests = [];
  const context = vm.createContext({
    window: {}, navigator: {}, console, AbortController, URLSearchParams, URL, TextEncoder,
    FormData, Blob, setTimeout: (...args) => { const timer = setTimeout(...args); timer.unref(); return timer; }, clearTimeout,
    confirm: message => { prompts.push(message); return allow; },
  });
  for (const name of fs.readdirSync(path.join(root, 'features'))) {
    if (name.endsWith('.js')) vm.runInContext(fs.readFileSync(path.join(root, 'features', name), 'utf8'), context);
  }
  vm.runInContext(fs.readFileSync(path.join(root, 'app.js'), 'utf8'), context);
  const app = context.scrapperApp();
  app.session = { loaded: true, role: 'admin', is_admin: true };
  app.manualImportUrl = 'https://recipes.example/synthetic';
  app.recipeDetail.data = { id: 7, name: 'Synthetic soup', ingredients: [{}, {}, {}] };
  app.recipeDetail.show = true;
  app.audit.withAi = true;
  app.audit.summary.empty_recipe_count = 1;
  app.audit.data.data_gaps.no_nutrition = [{ id: 7 }];
  app.audit.data.data_gaps.no_image = [{ id: 7 }];
  app.pending = [{ url: 'https://recipes.example/synthetic' }];
  app.junkItems = { items: [{ url: 'https://recipes.example/synthetic' }] };
  app.failedDownloads = [{ url: 'https://recipes.example/synthetic' }];
  app.showToast = () => {};
  app.api = async (method, url, body) => { requests.push({ method, url, body }); return {}; };
  app.fetchWithTimeout = async (url, options) => { requests.push({ method: options.method, url, body: options.body }); return {}; };
  return { app, prompts, requests };
}

function imageInput() {
  const file = new Blob(['synthetic photo'], { type: 'image/png' });
  file.name = 'synthetic.png';
  return { target: { files: [file], value: 'synthetic.png' } };
}

const actions = [
  ['URL import', app => app.importUrl()],
  ['file import', app => app.importRecipeFile(imageInput())],
  ['photo scan', app => app.scanPendingPhoto({ url: 'synthetic', visibility: 'global' }, imageInput())],
  ['pending reanalysis', app => app.reanalyzeOne({ url: 'synthetic' })],
  ['pending save with analysis', app => app.resolveItem({ url: 'synthetic', _name: 'Synthetic soup', _type: 'Suppe', content_type: 'recipe' }, 'save')],
  ['pending batch reanalysis', app => app.reanalyzeAll()],
  ['history reanalysis', app => app.reanalyzeHistoryOne({ url: 'synthetic' })],
  ['history dry-run', app => app.reanalyzeHistoryAll(true)],
  ['junk reanalysis', app => app.reanalyzeJunkOnly()],
  ['junk cleanup', app => app.cleanupAllJunk()],
  ['recipe image', app => app.generateRecipeImage()],
  ['image backfill', app => app.startRecipeImageBackfill()],
  ['ingredient extraction', app => app.extractIngredients()],
  ['nutrition', app => app.computeNutrition()],
  ['bulk nutrition', app => app.bulkComputeNutrition()],
  ['recover empty', app => app.recoverEmpty()],
  ['detail rescrape', app => app.rescrapeFromDetailModal()],
  ['audit rescrape', app => app.rescrapeRecipe(7)],
  ['batch rescrape', app => app.rescrapeBulkRecipeIds([7, 8], 'mit fehlenden Schritten')],
  ['missing-image rescrape', app => app.rescrapeBulkNoImage()],
  ['AI audit', app => app.loadAudit({ requestAI: true })],
  ['AI sanity', app => app.startAiSanity()],
  ['PDF dry-run with extraction', app => app.runAdminPdf(true)],
];

for (const [name, invoke] of actions) {
  test(`cancelling ${name} sends nothing and discloses recipient and scope`, async () => {
    const { app, prompts, requests } = createApp(false);
    const before = JSON.stringify(app.recipeDetail.data);
    await invoke(app);
    assert.equal(prompts.length, 1);
    assert.match(prompts[0], /OpenAI/);
    assert.match(prompts[0], /übermittelt/);
    assert.match(prompts[0], /personenbezogene/);
    assert.match(prompts[0], /für diese Aktion/);
    assert.equal(requests.length, 0);
    assert.equal(JSON.stringify(app.recipeDetail.data), before);
    assert.equal(app.manualImporting, false);
    assert.equal(app.admin.pdf.running, false);
    assert.equal(app.audit.loading, false);
  });
}

test('consent is requested anew for every URL import and is scoped to that JSON request', async () => {
  const { app, prompts, requests } = createApp(true);
  app.session = { loaded: true, role: 'full_user', is_admin: false };
  app.manualImportVisibility = 'global';
  await app.importUrl();
  await app.importUrl();
  assert.equal(prompts.length, 2);
  assert.equal(requests.length, 2);
  for (const request of requests) {
    assert.equal(request.url, '/api/pending/import-url');
    assert.equal(request.body.visibility, 'private');
    assert.equal(request.body.ai_processing_consent, consent);
  }
});

test('file and photo-scan approvals put consent alongside the multipart file', async () => {
  for (const method of ['importRecipeFile', 'scanPendingPhoto']) {
    const { app, requests } = createApp(true);
    const event = imageInput();
    if (method === 'scanPendingPhoto') await app[method]({ url: 'synthetic', visibility: 'global' }, event);
    else await app[method](event);
    const uploads = requests.filter(request => request.method === 'POST');
    assert.equal(uploads.length, 1);
    assert.equal(uploads[0].body.get('ai_processing_consent'), consent);
    assert.equal(uploads[0].body.get('file').name, 'synthetic.png');
    assert.equal(event.target.value, '');
  }
});

test('an explicit AI audit carries query consent but automatic reload is read-only', async () => {
  const { app, requests, prompts } = createApp(true);
  await app.loadAudit({ requestAI: true });
  const first = new URL(requests[0].url, 'https://local.invalid');
  assert.equal(first.searchParams.get('with_ai'), 'true');
  assert.equal(first.searchParams.get('ai_processing_consent'), consent);
  await app.loadAudit();
  const second = new URL(requests[1].url, 'https://local.invalid');
  assert.equal(second.searchParams.has('with_ai'), false);
  assert.equal(second.searchParams.has('ai_processing_consent'), false);
  assert.equal(prompts.length, 1);
});

test('saving a pending import carries consent; skipping it remains possible without permission', async () => {
  const item = { url: 'synthetic', _name: 'Synthetic soup', _type: 'Suppe', content_type: 'recipe' };
  const accepted = createApp(true);
  await accepted.app.resolveItem(item, 'save');
  assert.equal(accepted.requests.length, 1);
  assert.equal(accepted.requests[0].body.ai_processing_consent, consent);
  const declined = createApp(false);
  await declined.app.resolveItem(item, 'skip');
  assert.equal(declined.prompts.length, 0);
  assert.equal(declined.requests.length, 1);
  assert.equal(declined.requests[0].body.action, 'skip');
  assert.equal(declined.requests[0].body.ai_processing_consent, undefined);
});

test('reading recipes and a plain audit needs no AI permission', async () => {
  const { app, requests, prompts } = createApp(false);
  app.audit.withAi = false;
  app.api = async (method, url, body) => { requests.push({ method, url, body }); return {}; };
  await app.loadAudit({ requestAI: true });
  app.prefetchRecipeDetail(7);
  await Promise.resolve();
  assert.equal(prompts.length, 0);
  assert.equal(requests.length, 2);
  assert.ok(requests.every(request => request.method === 'GET' && !request.body));
});

test('a batch rescrape confirms once and carries consent on each selected recipe', async () => {
  const { app, requests, prompts } = createApp(true);
  await app.rescrapeBulkRecipeIds([7, 8], 'mit fehlenden Schritten');
  const writes = requests.filter(request => request.method === 'POST');
  assert.equal(writes.length, 2);
  assert.ok(writes.every(request => request.body.ai_processing_consent === consent));
  assert.equal(prompts.length, 1, 'The post-action audit must not start another AI request');
  assert.ok(requests.filter(request => request.method === 'GET').every(request => !request.url.includes('with_ai')));
});

test('PDF processing without recipe extraction stays available without AI consent', async () => {
  const { app, requests, prompts } = createApp(false);
  app.admin.pdf.extract_recipe_data = false;
  app.loadPdfPreflight = async () => { app.admin.pdf.preflight = { ok: true }; };
  await app.runAdminPdf(true);
  assert.equal(prompts.length, 0);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].body.extract_recipe_data, false);
  assert.equal(requests[0].body.ai_processing_consent, undefined);
});

test('PDF dry-run extraction includes permission, with no persistent setting', async () => {
  const { app, requests, prompts } = createApp(true);
  app.loadPdfPreflight = async () => { app.admin.pdf.preflight = { ok: true }; };
  await app.runAdminPdf(true);
  assert.equal(prompts.length, 1);
  assert.equal(requests[0].body.ai_processing_consent, consent);
  assert.equal(requests[0].body.dry_run, true);
  assert.equal(app.admin.pdf.ai_processing_consent, undefined);
});
