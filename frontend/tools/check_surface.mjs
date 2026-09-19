import assert from 'node:assert/strict';
import { surfaceMetrics } from '../src/surfaceMetrics.js';

const quoted = (row) => ({ bid: 9, ask: 9.1, t: 0.25, ...row });
const month = { id: '202609', code: 'MO2609', calendar_days: 8, trading_days: 6, forward: 100 };
const rows = [
  // OTM puts below F and OTM calls at/above F.  The x=1 point is a direct C.
  quoted({ month: '202609', cp: 'P', strike: 90, forward: 100, iv: 0.30, delta: -0.35 }),
  // Only the non-selected C exists at 95: retain the OTM-P gap as null.
  quoted({ month: '202609', cp: 'C', strike: 95, forward: 100, iv: 0.23, delta: 0.70 }),
  quoted({ month: '202609', cp: 'C', strike: 100, forward: 100, iv: 0.20, delta: 0.50 }),
  quoted({ month: '202609', cp: 'C', strike: 110, forward: 100, iv: 0.28, delta: 0.10 }),
  // Call 25D interpolation: delta .35 / .15 gives IV .22 / .26 => .24.
  quoted({ month: '202609', cp: 'C', strike: 103, forward: 100, iv: 0.22, delta: 0.35 }),
  quoted({ month: '202609', cp: 'C', strike: 107, forward: 100, iv: 0.26, delta: 0.15 }),
  // Put 25D interpolation: -0.35 / -0.15 gives IV .28 / .22 => .25.
  quoted({ month: '202609', cp: 'P', strike: 92, forward: 100, iv: 0.28, delta: -0.35 }),
  quoted({ month: '202609', cp: 'P', strike: 98, forward: 100, iv: 0.22, delta: -0.15 }),
  // A too-wide quote is retained as a visible gap, but not counted or used.
  quoted({ month: '202609', cp: 'C', strike: 115, forward: 100, iv: 0.40, bid: 1, ask: 2 }),
];

const [metrics] = surfaceMetrics(rows, [month]);
assert.deepEqual(Object.keys(metrics), [
  'id', 'code', 'calendar_days', 'trading_days', 'forward', 'atm_iv',
  'call25_iv', 'put25_iv', 'skew25_pp', 'points', 'valid_count',
]);
assert.equal(metrics.forward, 100);
assert.equal(metrics.atm_iv, 0.20);
assert.equal(metrics.call25_iv, 0.24);
assert.equal(metrics.put25_iv, 0.25);
assert.ok(Math.abs(metrics.skew25_pp - 1) < 1e-12);
assert.equal(metrics.valid_count, 7);
assert.deepEqual(metrics.points.find((point) => point.strike === 95), { x: 0.95, iv: null, cp: 'P', strike: 95 });
assert.deepEqual(metrics.points.at(-1), { x: 1.15, iv: null, cp: 'C', strike: 115 });

const noExtrapolationRows = [
  quoted({ month: '202609', cp: 'P', strike: 90, forward: 100, iv: 0.30, delta: -0.35 }),
  // A known but invalid ATM quote must stay empty despite valid points around it.
  quoted({ month: '202609', cp: 'C', strike: 100, forward: 100, iv: 0.20, bid: 0 }),
  quoted({ month: '202609', cp: 'C', strike: 105, forward: 100, iv: 0.24, delta: 0.30 }),
  // This quote is valid, but its delta is beyond the available call 25D wing.
  quoted({ month: '202609', cp: 'C', strike: 110, forward: 100, iv: 0.28, delta: 0.40 }),
];
const [limited] = surfaceMetrics(noExtrapolationRows, [month]);
assert.equal(limited.atm_iv, null);
assert.equal(limited.call25_iv, null);
assert.equal(limited.put25_iv, null);
assert.equal(limited.skew25_pp, null);

console.log('surface metrics hand-calculation and validity checks passed');
