import assert from 'node:assert/strict';
import test from 'node:test';
import { ensureExternalDistribution, EXTERNAL_GROUP_ID, compactApiError, selectExactBuild } from './testflight-ensure.mjs';

const NOTES = 'Backend 1.9.0: Mein Konto, Passwort, Sitzungen, Benutzerverwaltung und Einkaufsmengen prüfen.';
test('existing-build selection never substitutes another build, version or platform', () => {
  const build = { type: 'builds', id: 'exact', attributes: { version: '11601', uploadedDate: '2026-10-07T21:48:37Z' }, relationships: { preReleaseVersion: { data: { id: 'version' } } } };
  const payload = { data: [build], included: [{ type: 'preReleaseVersions', id: 'version', attributes: { version: '1.2.0', platform: 'IOS' } }] };
  const selection = { buildNumber: '11601', marketingVersion: '1.2.0', uploadStartedAt: null };
  assert.equal(selectExactBuild(payload, selection), build);
  assert.equal(selectExactBuild(payload, { ...selection, buildNumber: '11602' }), null);
  assert.equal(selectExactBuild(payload, { ...selection, marketingVersion: '1.2.1' }), null);
  assert.equal(selectExactBuild(payload, { ...selection, uploadStartedAt: Date.parse('2026-10-08T00:00:00Z') }), null);
  payload.included[0].attributes.platform = 'MAC_OS';
  assert.equal(selectExactBuild(payload, selection), null);
  payload.included = [];
  assert.equal(selectExactBuild(payload, selection), null);
});

test('duplicate exact build matches fail closed', () => {
  const build = { type: 'builds', id: 'exact', attributes: { version: '11601' }, relationships: { preReleaseVersion: { data: { id: 'version' } } } };
  const payload = { data: [build, { ...build, id: 'duplicate' }], included: [{ type: 'preReleaseVersions', id: 'version', attributes: { version: '1.2.0', platform: 'IOS' } }] };
  assert.throws(() => selectExactBuild(payload, { buildNumber: '11601', marketingVersion: '1.2.0', uploadStartedAt: null }), /Ambiguous/);
});
test('API diagnostics never expose contact or demo data from free-form errors', () => {
  const message = compactApiError({ errors: [{ status: '409', code: 'ENTITY_ERROR.ATTRIBUTE.REQUIRED', title: 'Private Reviewer', detail: 'demo-password contact@example.invalid' }, { code: 'echoed secret' }] }, 'POST /v1/betaAppReviewSubmissions returned HTTP 409');
  assert.equal(message, 'POST /v1/betaAppReviewSubmissions returned HTTP 409 (ENTITY_ERROR.ATTRIBUTE.REQUIRED)');
});
function fixture(options = {}) {
  const writes = [], calls = [];
  let assigned = !!options.assigned;
  let autoNotify = !!options.autoNotify;
  let state = options.state || 'READY_FOR_BETA_SUBMISSION';
  let review = options.review || null;
  let notes = options.notes === undefined ? [] : options.notes;
  const build = { type: 'builds', id: 'build-exact', attributes: { version: '11401', processingState: 'VALID', expired: false, buildAudienceType: 'APP_STORE_ELIGIBLE', ...options.build } };
  const reviewMetadata = { contactFirstName: 'Synthetic', contactLastName: 'Reviewer', contactEmail: 'test@example.invalid', contactPhone: '000000000', demoAccountRequired: false, ...options.metadata };
  const request = async (raw, init = {}) => {
    const url = new URL(raw, 'https://api.appstoreconnect.apple.com');
    const path = url.pathname; const method = init.method || 'GET';
    const body = init.body && JSON.parse(init.body);
    calls.push({ path, method });
    if (method !== 'GET') writes.push({ path, method, body });
    if (options.permissionFailure && method !== 'GET') throw Object.assign(new Error('403 FORBIDDEN'), { status: 403 });
    if (method === 'GET' && path === `/v1/betaGroups/${EXTERNAL_GROUP_ID}`) return { data: { type: 'betaGroups', id: options.groupId || EXTERNAL_GROUP_ID, attributes: { name: options.groupName || 'Privater Test', isInternalGroup: !!options.internal } } };
    if (path === `/v1/betaGroups/${EXTERNAL_GROUP_ID}/app`) return { data: { type: 'apps', id: options.groupApp || 'app-exact' } };
    if (path === '/v1/builds/build-exact/app') return { data: { type: 'apps', id: options.buildApp || 'app-exact' } };
    if (path === '/v1/betaGroups') {
      assert.equal(url.searchParams.get('filter[app]'), 'app-exact');
      assert.equal(url.searchParams.get('filter[builds]'), build.id);
      return { data: options.otherGroup ? [{ id: 'unrelated-group' }] : assigned ? [{ id: EXTERNAL_GROUP_ID }] : [] };
    }
    if (path === '/v1/builds/build-exact/individualTesters') return { data: options.individuals ? [{ id: 'unrelated-tester' }] : [] };
    if (path === `/v1/betaGroups/${EXTERNAL_GROUP_ID}/betaTesters`) return { data: options.noTesters ? [] : [{ id: 'existing-tester', attributes: { state: 'ACCEPTED' } }] };
    if (path === '/v1/builds/build-exact/buildBetaDetail') return { data: { type: 'buildBetaDetails', id: 'detail-exact', attributes: { externalBuildState: state, autoNotifyEnabled: autoNotify } } };
    if (path === '/v1/betaAppReviewSubmissions') {
      if (method === 'POST') {
        assert.equal(body.data.relationships.build.data.id, build.id);
        if (options.conflict) { review = options.conflictAccepted ? 'WAITING_FOR_REVIEW' : null; throw Object.assign(new Error('409 CONFLICT'), { status: 409 }); }
        review = 'WAITING_FOR_REVIEW'; state = 'WAITING_FOR_BETA_REVIEW';
      }
      assert.equal(method === 'POST' ? build.id : url.searchParams.get('filter[build]'), build.id);
      return { data: options.reviews || (review ? [{ id: 'review-exact', attributes: { betaReviewState: review } }] : []) };
    }
    if (path === '/v1/apps/app-exact/betaAppLocalizations') return { data: options.missingDescription ? [] : [{ attributes: { locale: 'de-DE', description: 'Synthetic description', feedbackEmail: 'feedback@example.invalid' } }] };
    if (path === '/v1/apps/app-exact/betaAppReviewDetail') return { data: { attributes: reviewMetadata } };
    if (path === '/v1/builds/build-exact/betaBuildLocalizations') return { data: notes };
    if (path === '/v1/betaBuildLocalizations' || path === '/v1/betaBuildLocalizations/notes-existing') {
      assert.ok(['POST', 'PATCH'].includes(method));
      if (method === 'POST') assert.equal(body.data.relationships.build.data.id, build.id);
      notes = [{ id: 'notes-existing', attributes: { locale: 'de-DE', whatsNew: body.data.attributes.whatsNew } }];
      return { data: notes[0] };
    }
    if (path === `/v1/betaGroups/${EXTERNAL_GROUP_ID}/relationships/builds`) {
      if (method === 'POST') { assert.deepEqual(body.data, [{ type: 'builds', id: build.id }]); if (!options.ignoreAssignment) assigned = true; }
      if (options.paginate && !url.searchParams.has('cursor')) return { data: [], links: { next: options.unsafePagination || raw + '&cursor=next' } };
      return { data: assigned ? [{ type: 'builds', id: build.id }] : [] };
    }
    if (path === '/v1/buildBetaDetails/detail-exact' && method === 'PATCH') { if (!options.ignoreAutoNotify) autoNotify = body.data.attributes.autoNotifyEnabled; return {}; }
    if (path === '/v1/buildBetaNotifications' && method === 'POST') {
      assert.equal(body.data.relationships.build.data.id, build.id);
      if (!options.notificationLag) state = 'IN_BETA_TESTING';
      return {};
    }
    throw new Error(`Unexpected request ${method} ${path}`);
  };
  return { build, writes, calls, run: () => ensureExternalDistribution({ request, build, appId: 'app-exact', whatToTest: NOTES }) };
}

test('external release adds only the authorized build/group and submits review without claiming availability', async () => {
  const f = fixture(); const result = await f.run();
  assert.equal(result.externalGroupId, EXTERNAL_GROUP_ID);
  assert.equal(result.betaReviewSubmitted, true);
  assert.equal(result.externalStatus, 'REVIEW_PENDING');
  assert.equal(result.externalTestingAvailable, false);
  assert.equal(result.externalNotificationSent, false);
  assert.equal(result.externalAutoNotifyEnabled, true);
  assert.equal(f.writes[0].body.data.attributes.locale, 'de-DE');
  assert.equal(f.writes[0].body.data.attributes.whatsNew, NOTES);
  assert.ok(f.writes.every(item => !/betaTesters|invitations|appStoreVersions|betaAppReviewDetails/.test(item.path)));
});

test('repeating a pending review is idempotent and never creates a second invitation or submission', async () => {
  const f = fixture(); await f.run(); const count = f.writes.length;
  const second = await f.run();
  assert.equal(f.writes.length, count);
  assert.equal(second.betaReviewSubmitted, false);
  assert.equal(second.externalStatus, 'REVIEW_PENDING');
});

test('approved ready build notifies existing testers once and verifies testing state', async () => {
  const f = fixture({ state: 'READY_FOR_BETA_TESTING', review: 'APPROVED' });
  const result = await f.run();
  assert.equal(result.externalNotificationSent, true);
  assert.equal(result.externalTestingAvailable, true);
  assert.equal(result.externalStatus, 'TESTING');
  await f.run();
  assert.equal(f.writes.filter(item => item.path === '/v1/buildBetaNotifications').length, 1);
  assert.equal(f.writes.filter(item => item.path === '/v1/betaAppReviewSubmissions').length, 0);
});

test('notification accepted with lag never masquerades as confirmed external availability', async () => {
  const f = fixture({ state: 'READY_FOR_BETA_TESTING', review: 'APPROVED', notificationLag: true });
  const result = await f.run();
  assert.equal(result.externalNotificationSent, true);
  assert.equal(result.externalTestingAvailable, false);
  assert.equal(result.externalStatus, 'NOT_YET_TESTING');
});

for (const options of [
  { internal: true }, { groupApp: 'wrong-app' }, { buildApp: 'wrong-app' }, { groupId: 'wrong-group' },
  { groupName: 'Other group' }, { noTesters: true }, { otherGroup: true }, { individuals: true },
  { build: { processingState: 'PROCESSING' } }, { build: { expired: true } }, { build: { buildAudienceType: 'INTERNAL_ONLY' } },
  { state: 'MISSING_EXPORT_COMPLIANCE' }, { state: 'BETA_REJECTED' }, { state: 'UNKNOWN' }, { review: 'REJECTED' },
]) test(`external preflight refuses ${JSON.stringify(options)} without mutations`, async () => {
  const f = fixture(options); await assert.rejects(f.run()); assert.equal(f.writes.length, 0);
});

test('missing review fields are reported by name without exposing metadata or changing contacts', async () => {
  const f = fixture({ metadata: { contactEmail: '', demoAccountRequired: true, demoAccountName: 'private-demo-account', demoAccountPassword: '' } });
  await assert.rejects(f.run(), error => {
    assert.match(error.message, /betaAppReviewDetails.contactEmail/);
    assert.match(error.message, /betaAppReviewDetails.demoAccountPassword/);
    assert.doesNotMatch(error.message, /private-demo-account|Synthetic|example.invalid/); return true;
  });
  assert.equal(f.writes.length, 0);
});

test('existing German notes are updated on the selected build only', async () => {
  const f = fixture({ notes: [{ id: 'notes-existing', attributes: { locale: 'de-DE', whatsNew: 'old notes' } }] });
  await f.run();
  assert.equal(f.writes[0].path, '/v1/betaBuildLocalizations/notes-existing');
  assert.equal(f.writes[0].method, 'PATCH');
});

test('pagination finds an existing build assignment and blocks foreign pagination origins', async () => {
  const f = fixture({ assigned: true, paginate: true }); await f.run();
  assert.ok(!f.writes.some(item => item.path.includes('relationships/builds')));
  const hostile = fixture({ assigned: true, paginate: true, unsafePagination: 'https://example.invalid/v1/steal' });
  await assert.rejects(hostile.run(), /Unsafe/);
  assert.ok(!hostile.calls.some(item => item.path.includes('steal')));
  assert.equal(hostile.writes.length, 0);
});

test('ambiguous or unknown review states are rejected before mutations', async () => {
  for (const reviews of [
    [{ attributes: { betaReviewState: 'APPROVED' } }, { attributes: { betaReviewState: 'REJECTED' } }],
    [{ attributes: { betaReviewState: 'UNRECOGNIZED' } }],
  ]) {
    const f = fixture({ reviews });
    await assert.rejects(f.run(), /review/);
    assert.equal(f.writes.length, 0);
  }
});

test('pagination user information and fragments are rejected before mutations', async () => {
  for (const unsafePagination of [
    'https://user:password@api.appstoreconnect.apple.com/v1/page',
    'https://api.appstoreconnect.apple.com/v1/page#fragment',
  ]) {
    const f = fixture({ paginate: true, unsafePagination });
    await assert.rejects(f.run(), /Unsafe/);
    assert.equal(f.writes.length, 0);
  }
});

test('unconfirmed assignment or auto-notify cannot submit review', async () => {
  for (const options of [{ ignoreAssignment: true }, { ignoreAutoNotify: true }]) {
    const f = fixture(options); await assert.rejects(f.run());
    assert.ok(!f.writes.some(item => item.path === '/v1/betaAppReviewSubmissions'));
  }
});

test('a submission conflict succeeds only after reading the actual existing review', async () => {
  const f = fixture({ conflict: true, conflictAccepted: true });
  assert.equal((await f.run()).externalStatus, 'REVIEW_PENDING');
  await assert.rejects(fixture({ conflict: true }).run(), /409/);
});

test('insufficient API key permissions are surfaced without changing group or submitting review', async () => {
  const f = fixture({ permissionFailure: true });
  await assert.rejects(f.run(), /403 FORBIDDEN/);
  assert.ok(!f.writes.some(item => item.path.includes('relationships/builds') || item.path === '/v1/betaAppReviewSubmissions'));
});
