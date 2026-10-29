import assert from 'node:assert/strict';
import test from 'node:test';
import { watchVersion } from '../src/version-check.js';

const flush = () => new Promise(resolve => setImmediate(resolve));
function setup(module = 'futures', values = ['v1']) {
  const env = new EventTarget(), document = new EventTarget(), timers = new Map(), calls = [], states = [];
  let now = 0, next = 0;
  Object.assign(env, { document, Date: { now: () => now },
    setTimeout: (fn, delay) => { timers.set(++next, { fn, at: now + delay }); return next; },
    clearTimeout: id => timers.delete(id) });
  document.hidden = false;
  const request = async (url, options) => {
    calls.push({ url, signal: options.signal });
    const value = values.shift();
    if (value instanceof Error) throw value;
    if (typeof value === 'function') return value(options.signal);
    return new Response(JSON.stringify(value && typeof value === 'object' ? value : module === 'seats' ? { meta: { data_version: value } } : { version: value }));
  };
  const stop = watchVersion(module, state => states.push(state), env, request);
  return { calls, states, timers, stop,
    focus: () => env.dispatchEvent(new Event('focus')),
    hidden: value => { document.hidden = value; document.dispatchEvent(new Event('visibilitychange')); },
    advance: async ms => { now += ms; for (const [id, timer] of [...timers]) if (timer.at <= now) { timers.delete(id); timer.fn(); } await flush(); } };
}

test('baseline is silent; one active endpoint checks every five minutes and detects opaque changes/rollback', async () => {
  const x = setup('futures', ['v1', 'v0', 'v1']); await flush();
  assert.equal(x.calls[0].url, '/api/futures/v1/version');
  assert.deepEqual(x.states.at(-1), { updated: false, failed: false });
  x.focus(); x.focus(); await x.advance(299999); assert.equal(x.calls.length, 1);
  await x.advance(1); assert.deepEqual(x.states.at(-1), { updated: true, failed: false });
  await x.advance(300000); assert.deepEqual(x.states.at(-1), { updated: false, failed: false });
  x.stop(); assert.equal(x.timers.size, 0);
});

test('hidden pages pause and visibility/focus share throttling', async () => {
  const x = setup('options', ['v1', 'v2']); await flush();
  assert.equal(x.calls[0].url, '/api/options/v1/version');
  x.hidden(true); x.focus(); await x.advance(1000); assert.equal(x.calls.length, 1);
  x.hidden(false); x.focus(); await flush(); assert.equal(x.calls.length, 1);
  x.hidden(true); await x.advance(300000); assert.equal(x.calls.length, 1);
  x.hidden(false); await flush(); assert.equal(x.calls.length, 2); x.stop();
});

test('seat envelope uses data_version, not a date or batch fallback', async () => {
  const x = setup('seats', ['batch-a', 'batch-b']); await flush();
  assert.equal(x.calls[0].url, '/api/seats/v1/meta/status');
  await x.advance(300000); assert.equal(x.states.at(-1).updated, true); x.stop();
});

test('failure cannot establish baseline or invent an update and retains a known update', async () => {
  const x = setup('futures', [new Error('offline'), {}, 'v1', 'v2', new Error('offline')]); await flush();
  assert.deepEqual(x.states.at(-1), { updated: false, failed: true });
  await x.advance(300000); assert.deepEqual(x.states.at(-1), { updated: false, failed: true });
  await x.advance(300000); assert.deepEqual(x.states.at(-1), { updated: false, failed: false });
  await x.advance(300000); await x.advance(300000);
  assert.deepEqual(x.states.at(-1), { updated: true, failed: true }); x.stop();
});

test('module unmount aborts pending requests and ignores late responses', async () => {
  let resolve;
  const x = setup('futures', [() => new Promise(done => { resolve = done; })]);
  x.stop(); assert.equal(x.calls[0].signal.aborted, true);
  resolve(new Response(JSON.stringify({ version: 'old' }))); await flush();
  x.focus(); x.hidden(false); await x.advance(600000);
  assert.equal(x.calls.length, 1); assert.deepEqual(x.states, []); assert.equal(x.timers.size, 0);
});

test('hiding aborts an incomplete check and allows a fresh baseline on return', async () => {
  let resolve;
  const x = setup('futures', [() => new Promise(done => { resolve = done; }), 'new']);
  x.hidden(true); assert.equal(x.calls[0].signal.aborted, true);
  x.hidden(false); await flush();
  resolve(new Response(JSON.stringify({ version: 'old' }))); await flush();
  assert.equal(x.calls.length, 2); assert.deepEqual(x.states, [{ updated: false, failed: false }]); x.stop();
});
