// Exercise the component's ECharts configuration with the real SVG renderer.
// This checks chart output, not browser layout or React event interactions.
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import fs from 'node:fs';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const { build } = require(createRequire(require.resolve('vite')).resolve('esbuild'));
const React = require('react'), echarts = require('echarts');
const frontend = new URL('../', import.meta.url);
const source = fs.readFileSync(new URL('src/ExitLab.jsx', frontend), 'utf8') + '\nexport { Heatmap };';
const compiled = await build({ stdin: { contents: source, resolveDir: fileURLToPath(new URL('src', frontend)), loader: 'jsx' }, bundle: true, write: false, platform: 'node', format: 'cjs', external: ['react', 'echarts'], loader: { '.css': 'empty' }, jsx: 'automatic' });
const base = 'http://127.0.0.1:8790/api/options/v1/';
const snapshot = await (await fetch(base + 'snapshot')).json();
const row = snapshot.rows.filter(r => r.cp === 'C' && r.iv && r.t > 0).sort((a, b) => a.month.localeCompare(b.month) || Math.abs(a.strike - snapshot.spot) - Math.abs(b.strike - snapshot.spot))[0];
const response = await fetch(base + 'exit-analysis', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ snapshot_id: snapshot.snapshot_id, legs: [{ code: row.code, side: 'buy', quantity: 1 }], price_min: snapshot.spot * .95, price_max: snapshot.spot * 1.05, price_steps: 15, horizon_days: Math.min(7, row.t * 365), time_steps: 8, target: 7700, profit_target: 5000 }) });
assert.equal(response.status, 200);
const result = await response.json(), reports = [];
for (const displayMode of ['normal', 'stress']) {
  const chart = echarts.init(null, null, { renderer: 'svg', ssr: true, width: 920, height: 440 });
  // In the browser a container supplies these dimensions on resize.
  const resize = chart.resize.bind(chart);
  chart.resize = () => resize({ width: 920, height: 440 });
  globalThis.window = { addEventListener() {}, removeEventListener() {} };
  const hooks = { ...React, useRef: () => ({ current: {} }), useMemo: fn => fn(), useEffect: fn => { fn(); } };
  const mod = { exports: {} };
  const localRequire = name => name === 'react' ? hooks : name === 'echarts' ? { ...echarts, init: () => chart } : require(name);
  new Function('require', 'module', 'exports', compiled.outputFiles[0].text)(localRequire, mod, mod.exports);
  mod.exports.Heatmap({ result, displayMode, asof: snapshot.asof });
  const option = chart.getOption();
  const high = chart.convertToPixel({ seriesIndex: 0 }, [0, result.prices.length - 1]);
  const low = chart.convertToPixel({ seriesIndex: 0 }, [0, 0]);
  assert.ok(high[1] < low[1], JSON.stringify({ high, low }));
  assert.equal(option.series[0].data.length, result.cells.length);
  const data = chart.getModel().getSeriesByIndex(0).getData();
  for (let index = 0; index < data.count(); index++) {
    const pnl = option.series[0].data[index].value[2];
    const rgb = echarts.color.parse(data.getItemVisual(index, 'style').fill);
    if (pnl > 0) assert.ok(rgb[0] > rgb[1], 'profit must be red');
    if (pnl < 0) assert.ok(rgb[1] > rgb[0], 'loss must be green');
  }
  const svg = chart.renderToSVGString();
  fs.writeFileSync(new URL(`../runtime/exit-heatmap-${displayMode}.svg`, frontend), svg);
  reports.push({ mode: displayMode, cells: result.cells.length, high_price_above_low: true, color_scale: option.visualMap[0].max, svg_bytes: svg.length });
  chart.dispose();
}
assert.equal(reports[0].color_scale, reports[1].color_scale);
fs.writeFileSync(new URL('../runtime/exit-chart-validation.json', frontend), JSON.stringify(reports, null, 2));
console.log(JSON.stringify(reports));
