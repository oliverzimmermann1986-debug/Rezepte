import assert from 'node:assert/strict';
import test from 'node:test';
import { requirePruefdienst, RELEASE_CONTEXT } from './pruefdienst-status.mjs';

const sha = 'a'.repeat(40);
const options = { repository: 'owner/Rezepte', sha, token: 'synthetic-ci-token' };
const answer = statuses => async () => ({ ok: true, json: async () => ({ sha, statuses }) });

test('only the canonical successful status on the exact SHA permits release', async () => {
  const result = await requirePruefdienst({ ...options, fetcher: async (url, request) => {
    assert.equal(url, `https://api.github.com/repos/owner/Rezepte/commits/${sha}/status?per_page=100`);
    assert.equal(request.method, 'GET');
    assert.equal(request.redirect, 'error');
    return { ok: true, json: async () => ({ sha, statuses: [{ context: RELEASE_CONTEXT, state: 'success' }] }) };
  } });
  assert.deepEqual(result, { sha, context: RELEASE_CONTEXT, state: 'success' });
});

for (const state of ['pending', 'failure', 'error']) {
  test(`a ${state} canonical check blocks release`, async () => {
    await assert.rejects(requirePruefdienst({ ...options,
      fetcher: answer([{ context: RELEASE_CONTEXT, state }]) }), /not green/);
  });
}

test('partial stage successes and old Quality successes never substitute for release', async () => {
  await assert.rejects(requirePruefdienst({ ...options, fetcher: answer([
    { context: 'pruefdienst/server', state: 'success' }, { context: 'test', state: 'success' },
  ]) }), /missing or ambiguous/);
});

test('mismatched SHA and contradictory statuses fail closed', async () => {
  await assert.rejects(requirePruefdienst({ ...options, fetcher: async () => ({ ok: true,
    json: async () => ({ sha: 'b'.repeat(40), statuses: [{ context: RELEASE_CONTEXT, state: 'success' }] }) }) }), /Invalid commit/);
  await assert.rejects(requirePruefdienst({ ...options,
    fetcher: answer([{ context: RELEASE_CONTEXT, state: 'success' }, { context: RELEASE_CONTEXT, state: 'failure' }]) }), /ambiguous/);
});

test('API denial does not expose token or response body', async () => {
  await assert.rejects(requirePruefdienst({ ...options, fetcher: async () => ({ ok: false, status: 403,
    json: async () => { throw Error(options.token); } }) }), error =>
    error.message.includes('HTTP 403') && !error.message.includes(options.token));
});

test('missing credentials or an ambiguous ref do not make a request', async () => {
  let calls = 0;
  const fetcher = async () => { calls++; throw Error('unexpected request'); };
  await assert.rejects(requirePruefdienst({ ...options, token: '', fetcher }), /token required/);
  await assert.rejects(requirePruefdienst({ ...options, sha: 'main', fetcher }), /exact commit/);
  assert.equal(calls, 0);
});
