import assert from "node:assert/strict";
import { readFile, access } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";
import { selectedProduct, rememberProduct } from "../src/preferences.js";

const adapterSource = await readFile(new URL("../src/seats/adapter.ts", import.meta.url), "utf8");
const adapterJs = ts.transpileModule(adapterSource, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText.replaceAll("import.meta.env", "({})").replace("../transport.js", new URL("../src/transport.js", import.meta.url).href);
const { marketGroups, marketGroup, mapApiSnapshot, createApiAdapter, createJsonRequester } = await import(`data:text/javascript;base64,${Buffer.from(adapterJs).toString("base64")}`);

const output = new URL("../dist/", import.meta.url);

const fixture = {
  "/meta/options": { products: [], trade_date: "2026-07-28" },
  "/views/combination": { selected_date: "2026-07-28", metrics: [], signals: [], net_position_top: [], long_top: [], short_top: [], combinations: [], combination_signals: [], divergence_cards: [], research_summary: {} },
  "/views/market-signals": { metrics: [], anomalies: [], treemap: [], signal_counts: [], term_opposite_count: 8 },
  "/views/term-structure": { metrics: [], contracts: [], opposite_seats: [] },
  "/views/validation": { rules: [] },
};

test("seat notices separate selected-day coverage from full-history gaps and provenance", () => {
  const warnings = ["P2 backfill provenance unavailable; not certified", "main layer pending: price=67, seat=6196"];
  const map = (coverage, publishedWarnings = warnings) => mapApiSnapshot(
    { productCode: "RB.SHFE", tradeDate: "2026-10-28", scope: "all" },
    { meta: {}, data: fixture["/meta/options"] }, undefined, undefined,
    { meta: { warnings: publishedWarnings, complete: true, updated_at: new Date().toISOString() }, data: {
      ...fixture["/views/term-structure"], metrics: [{ net_position: 0, net_change: 0 }], main_contract_coverage: coverage,
    } },
  ).meta;
  const ready = [{ price_status: "ready", seat_status: "ready" }];
  const history = map(ready);
  assert.deepEqual(history.warnings, warnings);
  assert.match(history.notice, /本批次未关联上游修订凭证/);
  assert.match(history.notice, /历史主力覆盖缺口：价格 67、席位 6196 个品种交易日（非当日，两项可能重叠）/);
  assert.doesNotMatch(history.notice, /2026-10-28|pending|更新/);
  const partial = map([...ready, { product_code: "BC.INE", symbol: "BC2612", price_status: "ready", seat_status: "missing_seat" }]);
  assert.match(partial.notice, /2026-10-28 主力缺口：席位 1 个品种（BC.INE\/BC2612）/);
  assert.equal(partial.state, "partial");
  const unresolved = map([{ price_status: "main_unresolved", seat_status: "main_unresolved" }], []);
  assert.equal(unresolved.notice, "2026-10-28 主力缺口：价格 1 个品种、席位 1 个品种（未知品种/主力未确定）");
  assert.equal(unresolved.state, "partial");
  assert.equal(map(ready, []).state, "ready");
  assert.equal(map(ready, []).notice, "");
  const many = map(["BC", "RR", "RS", "CY"].map(product_code => ({ product_code, symbol: null, price_status: "main_unresolved", seat_status: "main_unresolved" })), []);
  assert.match(many.notice, /席位 4 个品种（BC\/主力未确定、RR\/主力未确定、RS\/主力未确定等）/);
  assert.doesNotMatch(many.notice, /CY|undefined|null/);
});

test("requests only the active view and revalidates cached chunks; history follows the loaded date/version", async (t) => {
  let version = "v1";
  const requests = [];
  t.mock.method(globalThis, "fetch", async (input, init) => {
    const url = new URL(input, "http://test");
    requests.push(url);
    const etag = `W/"${version}:${url.pathname}"`;
    if (init.headers.get("If-None-Match") === etag) return new Response(null, { status: 304 });
    const data = fixture[url.pathname.replace("/api/seats/v1", "")] ?? { rows: [] };
    return new Response(JSON.stringify({ meta: { data_version: version, batch_id: 1, complete: true }, data }), { headers: { ETag: etag } });
  });
  const adapter = createApiAdapter();
  const filters = { productCode: "RB.SHFE", tradeDate: "", scope: "all" };
  const first = await adapter.loadSnapshot(filters, undefined, "combination");
  assert.deepEqual(requests.map(url => url.pathname), ["/api/seats/v1/meta/options", "/api/seats/v1/views/combination"]);
  assert.equal(requests.at(-1).searchParams.get("detail"), "groups");
  assert.deepEqual(await adapter.loadSnapshot(filters, undefined, "combination"), first);
  requests.length = 0;
  const market = await adapter.loadSnapshot({ ...filters, productCode: "all" }, undefined, "market");
  assert.deepEqual(requests.map(url => url.pathname), ["/api/seats/v1/meta/options", "/api/seats/v1/views/market-signals"]);
  assert.equal(market.overview.signalCounts.at(-1).value, 8);
  assert.equal(requests.at(-1).searchParams.has("product_code"), false);
  assert.equal(market.meta.selectedProductLabel, "全市场");
  await adapter.loadSeatHistory("seat-a", undefined, "CU.SHFE");
  assert.equal(requests.at(-1).searchParams.get("product_code"), "CU.SHFE");
  assert.equal(requests.at(-1).searchParams.get("end_date"), "2026-07-28");
  requests.length = 0;
  await adapter.loadSnapshot(filters, undefined, "term");
  assert.deepEqual(requests.map(url => url.pathname), ["/api/seats/v1/meta/options", "/api/seats/v1/views/term-structure"]);
  assert.equal(requests.at(-1).searchParams.get("detail"), "contracts");
  requests.length = 0;
  await adapter.loadSnapshot(filters, undefined, "product");
  assert.deepEqual(requests.map(url => url.pathname), ["/api/seats/v1/meta/options", "/api/seats/v1/views/combination", "/api/seats/v1/views/validation"]);
  assert.equal(requests[1].searchParams.get("detail"), "positions");
  assert.ok(requests.slice(1).every(url => url.searchParams.get("product_code") === "RB.SHFE"));
  await adapter.loadSeatHistory("seat-a");
  assert.equal(requests.at(-1).searchParams.get("end_date"), "2026-07-28");
  assert.equal(requests.at(-1).searchParams.get("product_code"), "RB.SHFE");
  version = "v2"; // Same batch id, corrected publication.
  await assert.rejects(adapter.loadSeatHistory("seat-a"), /批次已更新/);
});

test("mixed publications retry once, persistent mixing fails, and a later refresh recovers", async (t) => {
  let calls = 0, alwaysMixed = false;
  t.mock.method(globalThis, "fetch", async (input) => {
    const path = new URL(input, "http://test").pathname.replace("/api/seats/v1", "");
    const version = (alwaysMixed || calls++ === 0) && path === "/meta/options" ? "old" : "new";
    return new Response(JSON.stringify({ meta: { data_version: version, batch_id: 1 }, data: fixture[path] }));
  });
  const adapter = createApiAdapter(), filters = { productCode: "RB.SHFE", tradeDate: "", scope: "all" };
  await adapter.loadSnapshot(filters);
  assert.equal(calls, 4);
  alwaysMixed = true;
  await assert.rejects(adapter.loadSnapshot(filters), /批次正在切换/);
  alwaysMixed = false;
  await adapter.loadSnapshot(filters);
});

test("client cache has a byte bound and retries an orphan 304 without a validator", async (t) => {
  const seen = [];
  let orphan = true;
  t.mock.method(globalThis, "fetch", async (url, init) => {
    seen.push({ url, validator: init.headers.get("If-None-Match") });
    if (url === "/orphan" && orphan) { orphan = false; return new Response(null, { status: 304 }); }
    return new Response(JSON.stringify({ value: "x".repeat(100) }), { headers: { ETag: 'W/"test"' } });
  });
  const request = createJsonRequester(600);
  await request("/first"); await request("/second"); await request("/first");
  assert.equal(seen.at(-1).validator, null);
  await request("/orphan");
  assert.equal(seen.filter(item => item.url === "/orphan").length, 2);
  assert.equal(seen.at(-1).validator, null);
});

test("lazy term loading retains reversal tags without fetching another page", () => {
  const wrap = data => ({ meta: { complete: true }, data });
  const result = mapApiSnapshot(
    { productCode: "RB.SHFE", tradeDate: "2026-07-28", scope: "all" },
    wrap(fixture["/meta/options"]), undefined, undefined,
    wrap({ contracts: [], opposite_seats: [], metrics: [], top_changes: [{ seat_id: "a", seat_name: "席位A", net_change: 100 }],
      signals: [{ seat_id: "a", signal_code: "reversal", trade_date: "2026-07-28", evidence_json: "{}", metric_value: 100 }],
    }),
  );
  assert.equal(result.termStructure.topChanges[0].isReversal, true);
  assert.ok(result.termStructure.topChanges[0].tags.some(tag => tag.name === "多空反转"));
});

test("unified static build keeps seats lazy and shares font/runtime", async () => {
  const html = await readFile(new URL("index.html", output), "utf8");
  assert.match(html, /<title>MINGJIE<\/title>/);
  const manifest = JSON.parse(await readFile(new URL(".vite/manifest.json", output), "utf8"));
  const seat = manifest["src/seats/Dashboard.tsx"];
  assert.ok(seat?.isDynamicEntry);
  const js = await readFile(new URL(seat.file, output), "utf8");
  assert.match(js, /席位动向/);
  assert.match(js, /历史对照/);
  assert.doesNotMatch(js, /react-server-dom-webpack|vinext\/server|demo-data-/);
  const visit = (key, seen = new Set()) => { if (seen.has(key)) return seen; seen.add(key); for (const k of manifest[key]?.imports || []) visit(k,seen); return seen; };
  assert.ok(![...visit("src/seats/Dashboard.tsx")].some(k => /chart-engine|Options|futures/.test(k)));
  const entry = Object.entries(manifest).find(([,v])=>v.isEntry)[0];
  assert.ok(![...visit(entry)].some(k=>/Dashboard|Options|futures|chart-engine/.test(k)));
  const {readdir} = await import("node:fs/promises");
  const assets=await readdir(new URL("assets/",output));
  assert.equal(assets.filter(x=>x.endsWith(".woff2")).length,1);
  await access(new URL("inter-OFL.txt",output));
});

test("six market groups cover every configured product exactly once", async () => {
  assert.deepEqual(Object.keys(marketGroups), ["黑色系", "有色金属", "贵金属", "能化", "农产品", "金融期货"]);
  const roots = Object.values(marketGroups).flatMap(value => value.split(" "));
  assert.equal(new Set(roots).size, roots.length);
  const products = await readFile(new URL("../../seats/config/products.csv", import.meta.url), "utf8");
  for (const row of products.trim().split(/\r?\n/).slice(1)) assert.notEqual(marketGroup(row.split(",")[0]), "未分类", row);
  assert.equal(marketGroup("PG.DCE"), "能化");
  assert.equal(marketGroup("UNKNOWN.EXCHANGE"), "未分类");
});

test("a balanced net position preserves published concentration and missing values", () => {
  const wrap = data => ({ meta: { complete: true }, data });
  const metric = { net_position: 0, net_change: 0, long_short_ratio: 1, long_cr5: null, short_cr5: 0.4, net_cr5: 0.6, f2_value: 0, f2_status: "ready" };
  const snapshot = mapApiSnapshot(
    { productCode: "RB.SHFE", scope: "all", tradeDate: "" },
    wrap({ products: [], trade_date: "2026-07-28" }),
    wrap({ metrics: [metric], signals: [], net_position_top: [], long_top: [], short_top: [], combinations: [], combination_signals: [], divergence_cards: [], research_summary: {} }),
    wrap({ anomalies: [], treemap: [], signal_counts: [] }),
    wrap({ contracts: [], opposite_seats: [] }), wrap({ rules: [] }),
  );
  const metrics = Object.fromEntries(snapshot.overview.metrics.map(item => [item.key, item.value]));
  assert.equal(metrics["net-cr5"], "60.0");
  assert.equal(metrics["long-cr5"], "—");
  assert.equal(metrics.f2, "0.0");
});

test("group matrix retains mixed, balanced, flat and incomplete positions without inventing alignment", () => {
  const wrap = data => ({ meta: { complete: true }, data });
  const base = { product_code: "RB.SHFE", root: "RB", exchange: "SHFE", display_name: "螺纹钢", net_position: 100, net_change: -20, long_count: 2, short_count: 0, flat_count: 0, missing_count: 0 };
  const cases = [
    { ...base, combination_id: "aligned" },
    { ...base, combination_id: "mixed", net_position: 0, long_count: 1, short_count: 1 },
    { ...base, combination_id: "partial", missing_count: 1 },
    { ...base, combination_id: "flat", net_position: 0, net_change: 0, long_count: 0, flat_count: 2 },
    { ...base, combination_id: "missing", net_position: null, net_change: null, long_count: 0, missing_count: 2 },
  ];
  const result = mapApiSnapshot(
    { productCode: "RB.SHFE", scope: "all", tradeDate: "" }, wrap(fixture["/meta/options"]),
    wrap({ ...fixture["/views/combination"], combination_positions: cases }),
  );
  const cells = result.combinationMatrix[0].signals;
  assert.equal(Object.keys(cells).length, 5);
  assert.equal(cells.aligned.aligned, true);
  for (const id of ["mixed", "partial", "flat", "missing"]) assert.equal(cells[id].aligned, false, id);
  assert.equal(cells.mixed.netPosition, 0);
  assert.equal(cells.flat.netChange, 0);
  assert.equal(cells.partial.missingCount, 1);
  assert.equal(cells.missing.netPosition, null);
});


test("term overview uses its own metrics and validation, preserving zero and unavailable percentiles", () => {
  const wrap = data => ({ meta: { complete: true }, data });
  const rules = [{ rule_code: "f2_position_ratio", current_percentile: 0 }, { rule_code: "net_cr5_concentration", current_percentile: null }];
  const result = mapApiSnapshot(
    { productCode: "RB.SHFE", tradeDate: "2026-07-28", scope: "all" }, wrap(fixture["/meta/options"]), undefined, undefined,
    wrap({ ...fixture["/views/term-structure"], metrics: [{ net_position: 20, net_change: 1, f2_value: .25, f2_status: "ready" }] }),
    wrap({ rules, library_cutoff: "2026-06-01" }),
  );
  assert.equal(result.overview.metrics.find(item => item.key === "f2").value, "25.0");
  assert.equal(result.researchSummary.f2Percentile, 0);
  assert.equal(result.researchSummary.netCr5Percentile, null);
  assert.equal(result.researchSummary.libraryCutoff, "2026-06-01");
  assert.equal(result.validation.length, 2);
});


test("shared product selection normalizes exchange codes and ignores all-market scope", (t) => {
  const saved = new Map();
  const original = Object.getOwnPropertyDescriptor(globalThis, "sessionStorage");
  Object.defineProperty(globalThis, "sessionStorage", { configurable: true, value: { getItem: key => saved.get(key) ?? null, setItem: (key, value) => saved.set(key, value) } });
  t.after(() => { if (original) Object.defineProperty(globalThis, "sessionStorage", original); else delete globalThis.sessionStorage; });
  assert.equal(selectedProduct("IM"), "IM");
  rememberProduct("CU.SHFE");
  assert.equal(selectedProduct("IM"), "CU");
  rememberProduct("all");
  assert.equal(selectedProduct(), "CU");
  rememberProduct("rb");
  assert.equal(selectedProduct(), "RB");
});

test("seat requests resolve a shared root through the published product catalog", async (t) => {
  const requests = [];
  t.mock.method(globalThis, "fetch", async (input) => {
    const url = new URL(input, "http://test"); requests.push(url);
    const data = url.pathname.endsWith("/meta/options")
      ? { products: [{ product_code: "CU.SHFE", root: "CU", exchange: "SHFE", display_name: "铜" }], trade_date: "2026-07-28" }
      : fixture[url.pathname.replace("/api/seats/v1", "")];
    return new Response(JSON.stringify({ meta: { data_version: "root-test", complete: true }, data }));
  });
  const adapter = createApiAdapter();
  const result = await adapter.loadSnapshot({ productCode: "CU", scope: "all", tradeDate: "" }, undefined, "term");
  assert.equal(result.meta.selectedProductLabel, "铜");
  for (const url of requests.filter(url => url.pathname.includes("/views/"))) assert.equal(url.searchParams.get("product_code"), "CU.SHFE");
  await assert.rejects(adapter.loadSnapshot({ productCode: "XX", scope: "all", tradeDate: "" }, undefined, "term"), /暂无席位数据/);
});
