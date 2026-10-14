// Execute the actual browser runtime with deterministic timers. No source mocks.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function runtime() {
  const timers = new Map();
  let next = 1;
  const context = {
    window: {}, navigator: {}, location: { protocol: 'https:' },
    setTimeout(callback, delay) { const id = next++; timers.set(id, { callback, delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../app/static/runtime.js'), 'utf8'), context);
  return {
    poller: context.window.RezepteRuntime.createPoller,
    timers,
    async fire() {
      assert.equal(timers.size, 1, 'exactly one pending poll');
      const [id, timer] = timers.entries().next().value;
      timers.delete(id);
      await timer.callback();
    },
    delay() { assert.equal(timers.size, 1); return timers.values().next().value.delay; },
  };
}
const settle = () => new Promise(resolve => setImmediate(resolve));

test('polling backs off when unchanged and returns to minimum after changes', async () => {
  const r = runtime();
  const answers = [false, false, true];
  const p = r.poller(async () => answers.shift(), { minDelay: 250, maxDelay: 1000, backoff: 2 });
  p.start(); await settle();
  assert.equal(r.delay(), 500);
  await r.fire(); assert.equal(r.delay(), 1000);
  await r.fire(); assert.equal(r.delay(), 250);
  p.stop(); assert.equal(r.timers.size, 0);
});

test('stop during an in-flight request prevents another request from being scheduled', async () => {
  const r = runtime();
  let complete;
  let calls = 0;
  const p = r.poller(() => { calls++; return new Promise(resolve => { complete = resolve; }); });
  p.start(); p.stop(); complete(true); await settle();
  assert.equal(calls, 1);
  assert.equal(p.running, false);
  assert.equal(r.timers.size, 0);
});

test('inactive page does not poll and wake resumes without duplicate timers', async () => {
  const r = runtime();
  let active = false, calls = 0;
  const p = r.poller(async () => { calls++; return true; }, { isActive: () => active, maxDelay: 8000 });
  p.start(); p.start(); await settle();
  assert.equal(calls, 0); assert.equal(r.delay(), 8000);
  active = true; p.wake(); p.wake();
  assert.equal(r.delay(), 0);
  await r.fire(); assert.equal(calls, 1);
  p.stop(); p.wake(); assert.equal(r.timers.size, 0);
});

test('temporary request failure backs off and polling recovers', async () => {
  const r = runtime();
  let calls = 0;
  const p = r.poller(async () => { if (++calls === 1) throw Error('offline'); return true; },
    { minDelay: 250, maxDelay: 1000, backoff: 2 });
  p.start(); await settle(); assert.equal(r.delay(), 500);
  await r.fire(); assert.equal(calls, 2); assert.equal(r.delay(), 250);
  p.stop();
});
