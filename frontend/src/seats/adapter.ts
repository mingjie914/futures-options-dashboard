import { conditionalFetch, createConditionalFetch } from '../transport.js';
import type {
  AnomalyRow,
  CombinationProductRow,
  CombinationRow,
  DashboardFilters,
  DashboardSnapshot,
  DashboardView,
  HistoryPoint,
  MetricCardData,
  NetPositionRow,
  OppositeSeatRow,
  ProductOption,
  SeatHistory,
  SeatRankingRow,
  SeatTag,
  SeatTagName,
  TermChangeRow,
  TermContractRow,
  Tone,
  TreemapItem,
  ValidationRuleRow,
} from "./types";

export interface DashboardClientAdapter {
  loadSnapshot(
    filters: DashboardFilters,
    signal?: AbortSignal,
    view?: DashboardView,
  ): Promise<DashboardSnapshot>;
  loadSeatHistory(seatId: string, signal?: AbortSignal, productCode?: string): Promise<SeatHistory>;
}

interface ApiMeta {
  data_version?: string;
  batch_id?: string | number | null;
  trade_date?: string | null;
  updated_at?: string | null;
  data_backend?: string;
  complete?: boolean;
  warnings?: string[];
  is_demo?: boolean;
}

interface ApiEnvelope<T> {
  meta: ApiMeta;
  data: T & { main_contract_coverage?: Array<{ product_code?: string; symbol?: string | null; price_status: string; seat_status: string }> };
}

interface ApiProduct {
  product_code: string;
  root: string;
  exchange: string;
  display_name: string;
}

interface ApiCombinationOption {
  combination_id: string;
  name: string;
  scope: string;
  member_count?: number;
}

interface ApiOptions {
  trade_date: string;
  products: ApiProduct[];
  combinations: ApiCombinationOption[];
}

interface ApiMetric {
  trade_date: string;
  product_code: string;
  root: string;
  exchange: string;
  total_long: number;
  total_short: number;
  net_position: number;
  long_change: number;
  short_change: number;
  net_change: number;
  seat_count: number;
  long_short_ratio: number | null;
  long_cr5: number | null;
  short_cr5: number | null;
  net_cr5: number | null;
  divergence_state: string;
  f2_value: number | null;
  f2_status: string;
}

interface ApiStaticTag {
  name: string;
  tone: string;
  source: string;
  confidence: number | null;
  valid_from: string | null;
  valid_to: string | null;
  evidence_json: string;
  note: string | null;
}

interface ApiSeatRow {
  product_code: string;
  seat_id: string;
  seat_name: string;
  long_position: number;
  long_change: number;
  short_position: number;
  short_change: number;
  net_position: number;
  net_change: number;
  abs_net_position: number;
  previous_net_position?: number | null;
  net_position_change?: number;
  entry_state?: string;
  net_share?: number | null;
  position_share?: number | null;
  tags?: ApiStaticTag[];
}

interface ApiSignal {
  trade_date: string;
  product_code: string;
  seat_id: string;
  seat_name: string;
  signal_code: string;
  direction: string;
  severity: "high" | "medium" | "low";
  metric_value: number | null;
  threshold_value: number | null;
  confidence: number | null;
  evidence_json: string;
  rule_version: string;
  tags?: ApiStaticTag[];
}

interface ApiCombinationView {
  selected_date: string;
  metrics: ApiMetric[];
  net_position_top: ApiSeatRow[];
  long_top: ApiSeatRow[];
  short_top: ApiSeatRow[];
  signals: ApiSignal[];
  insight: { code: string; text: string; confidence: number | null };
  combinations: Array<ApiCombinationOption & { members: string[]; is_system: boolean }>;
  combination_signals: Array<{
    product_code: string;
    root: string;
    exchange: string;
    display_name: string;
    combination_id: string;
    direction: "long" | "short";
    net_position: number;
    net_change: number;
    action_code: "add_long" | "reduce_long" | "add_short" | "reduce_short" | null;
    action_change: number | null;
  }>;
  combination_signal_product_count: number;
  combination_positions?: Array<{
    product_code: string; root: string; exchange: string; display_name: string; combination_id: string;
    net_position: number | null; net_change: number | null;
    long_count: number; short_count: number; flat_count: number; missing_count: number;
  }>;
  divergence_pair?: string[];
  divergence_cards: Array<{
    code: string;
    label: string;
    count: number;
    products: Array<{ product_code: string; display_name: string }>;
  }>;

}

interface ApiMarketView {
  term_opposite_count?: number;
  selected_date: string;
  metrics: ApiMetric[];
  anomalies: ApiSignal[];
  treemap: Array<{
    product_code: string;
    net_position: number;
    net_change: number;
    total_long: number;
    total_short: number;
    seat_count: number;
    long_cr5: number | null;
    short_cr5: number | null;
    divergence_state: string;
    root: string;
    exchange: string;
    display_name: string;
    open_interest_change_rate?: number | null;
    open_interest?: number | null;
    previous_open_interest?: number | null;
    previous_interest_date?: string | null;
    interest_contract_count?: number | null;
    previous_interest_contract_count?: number | null;
    interest_contracts_changed?: boolean;
  }>;
  signal_counts: Array<{ signal_code: string; severity: string; count: number }>;
}

interface ApiContract {
  product_code: string;
  contract_code: string;
  symbol: string;
  contract_month: number | null;
  contract_rank: number;
  total_long: number;
  total_short: number;
  net_position: number;
  close_price?: number | null;
  price_date?: string | null;
}

interface ApiTermSignal {
  trade_date: string;
  product_code: string;
  near_contract_code: string;
  far_contract_code: string;
  seat_id: string;
  seat_name: string;
  near_net_position: number;
  far_net_position: number;
  direction: string;
  min_leg_position: number;
  leg_count: number;
  legs_json: string;
  confidence: number | null;
  evidence_json: string;
  rule_version: string;
  tags?: ApiStaticTag[];
}

interface ApiTermView {
  signals?: ApiSignal[];
  metrics?: ApiMetric[];
  selected_date: string;
  contracts: ApiContract[];
  opposite_seats: ApiTermSignal[];
  top_changes?: ApiSeatRow[];
  min_leg_position: number;
}

interface ApiRule {
  trade_date: string;
  product_code: string;
  rule_code: string;
  horizon_days: number;
  signal_direction: string;
  current_percentile: number | null;
  sample_size: number;
  mean_return: number | null;
  median_return: number | null;
  win_rate: number | null;
  confidence_low: number | null;
  confidence_high: number | null;
  status: string;
  return_basis: string;
  rule_version: string;
  band: string;
  signal_label: string;
  confidence_label: string;
  library_cutoff: string | null;
}

interface ApiValidationView {
  library_cutoff?: string | null;
  selected_date: string;
  rules: ApiRule[];
}

interface ApiHistoryRow {
  trade_date: string;
  product_code: string;
  seat_id: string;
  seat_name: string;
  long_position: number;
  short_position: number;
  net_position: number;
}

interface ApiHistoryPayload {
  entity: string;
  entity_id: string;
  rows: ApiHistoryRow[];
}

const numberFormatter = new Intl.NumberFormat("zh-CN");

// Display grouping only; it does not change exchange identifiers or calculations.
export const marketGroups: Record<string, string> = {
  黑色系: "RB HC I J JM ZC SF SM SS WR",
  有色金属: "CU AL ZN PB NI SN AO AD BC SI LC PS",
  贵金属: "AU AG PT PD",
  能化: "SC FU LU BU PG RU NR BR L V PP EG EB BZ TA MA SA FG SH UR PF PX PR PL SP OP EC",
  农产品: "A B C CS M Y P OI RM RS SR CF CY AP CJ JD LH RR RI LR JR PM WH PK BB FB LG",
  金融期货: "IF IH IC IM T TF TS TL",
};
const groupByRoot = new Map(Object.entries(marketGroups).flatMap(([group, roots]) => roots.split(" ").map((root) => [root, group] as const)));
export const marketGroup = (productCode: string) => groupByRoot.get(productCode.split(".")[0].toUpperCase()) ?? "未分类";

const signalLabels: Record<string, string> = {
  reversal: "多空反转",
  position_spike: "持仓异动",
  term_opposite: "近远月反向",
  suspected_arbitrage: "疑似套利",
  new_top: "新进 Top",
  exit_top: "退出 Top",
  long_surge: "多头激增",
  long_drop: "多头骤减",
  short_surge: "空头激增",
  short_drop: "空头骤减",
};

const signalTags: Partial<Record<string, SeatTagName>> = {
  reversal: "多空反转",
  position_spike: "持仓异动",
  term_opposite: "近远月反向",
  suspected_arbitrage: "疑似套利",
};

const signalTones: Record<string, Tone> = {
  reversal: "negative",
  position_spike: "warning",
  term_opposite: "gold",
  suspected_arbitrage: "gold",
};

const supportedTagNames = new Set<SeatTagName>([
  "资金席位",
  "套保盘",
  "弱势席位",
  "重点跟踪",
  "疑似套利",
  "多空反转",
  "持仓异动",
  "近远月反向",
]);

function normalizeTone(value: string): Tone {
  const tones: Record<string, Tone> = {
    accent: "accent",
    highlight: "accent",
    gold: "gold",
    positive: "positive",
    negative: "negative",
    warning: "warning",
    muted: "neutral",
    neutral: "neutral",
  };
  return tones[value] ?? "neutral";
}

function ensureNotAborted(signal?: AbortSignal): void {
  if (signal?.aborted) throw new DOMException("请求已取消", "AbortError");
}

export function createJsonRequester(maxBytes?: number) {
  const request = maxBytes === undefined ? conditionalFetch : createConditionalFetch(maxBytes);
  return async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
    const response = await request(url, init);
    if (!response.ok) {
      let detail = "";
      try {
        const payload = await response.json();
        detail = payload.error?.message ?? (typeof payload.detail === "string" ? payload.detail : payload.detail?.message);
      } catch { /* A reverse proxy may return non-JSON errors. */ }
      throw new Error(detail || `数据请求失败（${response.status}）`);
    }
    return response.json() as Promise<T>;
  };
}

function safeEvidence(value: string): Record<string, unknown> {
  try {
    const parsed = JSON.parse(value) as unknown;
    return parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

function signalTag(signal: ApiSignal): SeatTag | null {
  const name = signalTags[signal.signal_code];
  if (!name) return null;
  const evidence = safeEvidence(signal.evidence_json);
  const previous = typeof evidence.previous_net_position === "number" ? evidence.previous_net_position : null;
  const current = typeof evidence.net_position === "number" ? evidence.net_position : signal.metric_value;
  const evidenceText = previous == null
    ? `${signalLabels[signal.signal_code] ?? signal.signal_code}，指标值 ${numberFormatter.format(current ?? 0)} 手。`
    : `净持仓由 ${numberFormatter.format(previous)} 手变为 ${numberFormatter.format(current ?? 0)} 手。`;
  return {
    name,
    tone: signalTones[signal.signal_code] ?? "warning",
    source: `规则 ${signal.rule_version}`,
    confidence: signal.confidence ?? 0,
    effectiveFrom: signal.trade_date,
    effectiveTo: signal.trade_date,
    evidence: evidenceText,
  };
}

function termTag(signal: ApiTermSignal): SeatTag {
  return {
    name: "近远月反向",
    tone: "gold",
    source: `规则 ${signal.rule_version}`,
    confidence: signal.confidence ?? 0,
    effectiveFrom: signal.trade_date,
    effectiveTo: signal.trade_date,
    evidence: `${signal.near_contract_code} 与 ${signal.far_contract_code} 的净持仓方向相反，双腿均达到 ${numberFormatter.format(signal.min_leg_position)} 手门槛。`,
  };
}

function staticTag(tag: ApiStaticTag): SeatTag | null {
  if (!supportedTagNames.has(tag.name as SeatTagName)) return null;
  const evidence = safeEvidence(tag.evidence_json);
  const evidenceText = typeof evidence.text === "string"
    ? evidence.text
    : tag.note || "该标签已由治理配置发布。";
  return {
    name: tag.name as SeatTagName,
    tone: normalizeTone(tag.tone),
    source: tag.source,
    confidence: tag.confidence ?? 0,
    effectiveFrom: tag.valid_from ?? "未限定",
    effectiveTo: tag.valid_to ?? "未限定",
    evidence: evidenceText,
  };
}

function productOptions(options: ApiOptions): ProductOption[] {
  return [
    { code: "all", label: "全市场", exchange: "ALL", },
    ...options.products.map((product) => ({
      code: product.product_code,
      label: product.display_name || product.product_code,
      exchange: product.exchange,
    })),
  ];
}

function selectedProductLabel(products: ProductOption[], productCode: string): string {
  return products.find((product) => product.code === productCode)?.label ?? productCode;
}

function metricCards(metrics: ApiMetric[]): MetricCardData[] {
  if (metrics.length !== 1) return [];
  const metric = metrics[0];
  const f2 = metric.f2_status !== "unavailable" && metric.f2_value != null ? metric : undefined;
  return [
    { key: "f2", label: "净持仓比率", value: f2 ? (f2.f2_value! * 100).toFixed(1) : "不可用", unit: f2 ? "%" : undefined, note: "净持仓 ÷（多头 + 空头）", tone: f2 && f2.f2_value! >= 0 ? "positive" : "negative", status: f2 ? "ready" : "unavailable" },
    { key: "ratio", label: "多空比", value: metric.long_short_ratio?.toFixed(2) ?? "—", unit: metric.long_short_ratio == null ? undefined : "x", note: "多头持仓 ÷ 空头持仓", tone: "accent" },
    { key: "long-cr5", label: "多头前五占比", value: metric.long_cr5 == null ? "—" : (metric.long_cr5 * 100).toFixed(1), unit: metric.long_cr5 == null ? undefined : "%", note: "前五席位多头持仓之和 ÷ 全部披露席位多头持仓", tone: "positive" },
    { key: "short-cr5", label: "空头前五占比", value: metric.short_cr5 == null ? "—" : (metric.short_cr5 * 100).toFixed(1), unit: metric.short_cr5 == null ? undefined : "%", note: "前五席位空头持仓之和 ÷ 全部披露席位空头持仓", tone: "negative" },
    { key: "net", label: "净持仓", value: numberFormatter.format(metric.net_position), unit: "手", note: `当日净变化 ${metric.net_change >= 0 ? "+" : ""}${numberFormatter.format(metric.net_change)} 手`, tone: metric.net_position >= 0 ? "positive" : "negative" },
    { key: "net-cr5", label: "净持仓前五占比", value: metric.net_cr5 == null ? "—" : (metric.net_cr5 * 100).toFixed(1), unit: metric.net_cr5 == null ? undefined : "%", note: "前五席位净持仓绝对值之和 ÷ 全部席位净持仓绝对值之和", tone: "gold" },
  ];
}

function tagsBySeat(
  signals: ApiSignal[],
  staticSources: Array<{ seat_id: string; tags?: ApiStaticTag[] }>,
): Map<string, SeatTag[]> {
  const result = new Map<string, SeatTag[]>();
  for (const source of staticSources) {
    const current = result.get(source.seat_id) ?? [];
    for (const apiTag of source.tags ?? []) {
      const tag = staticTag(apiTag);
      if (tag && !current.some((item) => item.name === tag.name && item.source === tag.source)) {
        current.push(tag);
      }
    }
    if (current.length) result.set(source.seat_id, current);
  }
  for (const signal of signals) {
    const tag = signalTag(signal);
    if (!tag) continue;
    const current = result.get(signal.seat_id) ?? [];
    if (!current.some((item) => item.name === tag.name)) current.push(tag);
    result.set(signal.seat_id, current);
  }
  return result;
}

function netRows(rows: ApiSeatRow[], tags: Map<string, SeatTag[]>): NetPositionRow[] {
  return rows.slice(0, 10).map((row, index) => ({
    rank: index + 1,
    seatId: row.seat_id,
    seatName: row.seat_name,
    direction: row.net_position >= 0 ? "多" : "空",
    netPosition: row.net_position,
    change: row.net_position_change ?? row.net_change,
    tags: tags.get(row.seat_id) ?? [],
  }));
}

function rankingRows(rows: ApiSeatRow[], direction: "多" | "空", tags: Map<string, SeatTag[]>): SeatRankingRow[] {
  const key = direction === "多" ? "long_position" : "short_position";
  const changeKey = direction === "多" ? "long_change" : "short_change";
  return rows.slice(0, 20).map((row, index) => ({
    rank: index + 1,
    seatId: row.seat_id,
    seatName: row.seat_name,
    position: row[key],
    change: row[changeKey],
    share: (row.position_share ?? 0) * 100,
    signal: tags.get(row.seat_id)?.[0]?.name,
    tags: tags.get(row.seat_id) ?? [],
  }));
}

function combinationRows(combinations: ApiCombinationView["combinations"]): CombinationRow[] {
  return combinations.map((combination) => ({
    id: combination.combination_id,
    name: combination.name,
    scope: combination.scope,
    memberCount: combination.member_count ?? 0,
    members: combination.members,
  }));
}

function combinationMatrixRows(view: ApiCombinationView): CombinationProductRow[] {
  const sizes = new Map(view.combinations.map(group => [group.combination_id, group.member_count ?? 0]));
  const rows = view.combination_positions ?? view.combination_signals.map(row => ({
    ...row, long_count: row.direction === "long" ? sizes.get(row.combination_id) ?? 0 : 0,
    short_count: row.direction === "short" ? sizes.get(row.combination_id) ?? 0 : 0, flat_count: 0, missing_count: 0,
  }));
  const products = new Map<string, CombinationProductRow>();
  for (const row of rows) {
    const product = products.get(row.product_code) ?? {
      productCode: row.product_code,
      root: row.root,
      label: row.display_name || row.product_code,
      exchange: row.exchange,
      signals: {},
    };
    product.signals[row.combination_id] = {
      combinationId: row.combination_id,
      netPosition: row.net_position,
      netChange: row.net_change,
      longCount: row.long_count,
      shortCount: row.short_count,
      flatCount: row.flat_count,
      missingCount: row.missing_count,
      aligned: row.missing_count === 0 && row.flat_count === 0 && (row.long_count > 0 && row.short_count === 0 || row.short_count > 0 && row.long_count === 0),
    };
    products.set(row.product_code, product);
  }
  return [...products.values()];
}

function anomalyRows(signals: ApiSignal[], tags: Map<string, SeatTag[]>): AnomalyRow[] {
  return signals.map((signal, index) => {
    const tag = signalTag(signal);
    const evidence = safeEvidence(signal.evidence_json);
    const previous = typeof evidence.previous_net_position === "number" ? evidence.previous_net_position : null;
    const current = typeof evidence.net_position === "number" ? evidence.net_position : signal.metric_value;
    const multiple = typeof evidence.multiple === "number" ? evidence.multiple : null;
    const detail = previous == null
      ? `增减 ${numberFormatter.format(signal.metric_value ?? 0)} 手${multiple == null ? "" : `，${multiple.toFixed(1)}× 均值`}`
      : `${numberFormatter.format(previous)} → ${numberFormatter.format(current ?? 0)} 手`;
    return {
      id: `${signal.trade_date}-${signal.product_code}-${signal.seat_id}-${signal.signal_code}-${index}`,
      type: signalLabels[signal.signal_code] ?? signal.signal_code,
      severity: signal.severity,
      seatId: signal.seat_id,
      seatName: signal.seat_name,
      productCode: signal.product_code,
      detail,
      metric: multiple == null ? "净持仓方向" : `${multiple.toFixed(1)}× 均值`,
      change: signal.metric_value ?? 0,
      tags: tags.get(signal.seat_id) ?? (tag ? [tag] : []),
      occurredAt: signal.trade_date,
    };
  });
}

function treemapRows(rows: ApiMarketView["treemap"], products: ProductOption[]): TreemapItem[] {
  return rows.map((row) => ({
      productCode: row.product_code,
      label: row.display_name || selectedProductLabel(products, row.product_code),
      group: marketGroup(row.product_code),
      change: row.net_change,
      openInterestChangeRate: row.open_interest_change_rate ?? null,
      openInterest: row.open_interest ?? null,
      previousOpenInterest: row.previous_open_interest ?? null,
      previousInterestDate: row.previous_interest_date ?? null,
      interestContractCount: row.interest_contract_count ?? null,
      previousInterestContractCount: row.previous_interest_contract_count ?? null,
      interestContractsChanged: row.interest_contracts_changed ?? false,
  }));
}

function contractRows(rows: ApiContract[]): TermContractRow[] {
  return rows.slice(0, 12).map((row) => ({
    contractCode: row.contract_code,
    label: row.symbol || row.contract_code,
    netPosition: row.net_position,
    isNear: row.contract_rank === 1,
    closePrice: row.close_price != null && Number.isFinite(row.close_price) && row.close_price > 0 ? row.close_price : null,
    priceDate: row.price_date ?? null,
  }));
}

function oppositeRows(rows: ApiTermSignal[], tags: Map<string, SeatTag[]>): OppositeSeatRow[] {
  return rows.map((row) => {
    let legs: OppositeSeatRow["legs"] = [];
    try {
      const parsed = JSON.parse(row.legs_json) as Array<{ contract_code: string; symbol: string; net_position: number }>;
      legs = parsed.filter((leg) => leg.net_position !== 0).map((leg) => ({ contractCode: leg.contract_code, label: leg.symbol || leg.contract_code, netPosition: leg.net_position }));
    } catch {
      legs = [];
    }
    return ({
    seatId: row.seat_id,
    seatName: row.seat_name,
    productCode: row.product_code,
    nearContractCode: row.near_contract_code,
    farContractCode: row.far_contract_code,
    nearDirection: row.near_net_position >= 0 ? "多" : "空",
    farDirection: row.far_net_position >= 0 ? "多" : "空",
    legs,
    tags: [
      termTag(row),
      ...(tags.get(row.seat_id) ?? []).filter((tag) => tag.name !== "近远月反向"),
    ],
    });
  });
}

function termChangeRows(rows: ApiSeatRow[] | undefined, tags: Map<string, SeatTag[]>): TermChangeRow[] {
  return (rows ?? []).slice(0, 10).map((row, index) => ({
    rank: index + 1,
    seatId: row.seat_id,
    seatName: row.seat_name,
    netChange: row.net_position_change ?? row.net_change,
    entryState: row.entry_state ?? "existing",
    isReversal: row.entry_state === "reversal" || (tags.get(row.seat_id)?.some((tag) => tag.name === "多空反转") ?? false),
    tags: tags.get(row.seat_id) ?? [],
  }));
}

function validationRows(rows: ApiRule[]): ValidationRuleRow[] {
  const factorLabels: Record<string, string> = {
    net_change_followthrough: "净持仓变化后续表现",
    net_cr5_concentration: "净持仓前五占比",
    f2_position_ratio: "净持仓比率",
  };
  return rows.map((row, index) => ({
    id: `${row.trade_date}-${row.product_code}-${row.rule_code}-${row.horizon_days}-${index}`,
    factor: factorLabels[row.rule_code] ?? row.rule_code,
    productCode: row.product_code,
    medianReturn: row.median_return == null ? null : row.median_return * 100,
    horizon: `${row.horizon_days}日`,
    percentile: row.current_percentile,
    returnValue: row.mean_return == null ? null : row.mean_return * 100,
    winRate: row.win_rate == null ? null : row.win_rate * 100,
    sampleSize: row.sample_size,
    band: row.band,
  }));
}

function signalCountRows(market: ApiMarketView, term: ApiTermView) {
  const rows = market.signal_counts.map((row) => ({
    label: signalLabels[row.signal_code] ?? row.signal_code,
    value: row.count,
    tone: (row.severity === "high" ? "negative" : row.severity === "medium" ? "warning" : "neutral") as Tone,
  }));
  const termCount = market.term_opposite_count ?? term.opposite_seats.length;
  if (termCount) rows.push({ label: "近远月反向", value: termCount, tone: "gold" });
  return rows.length ? rows : [{ label: "当前无规则异动", value: 0, tone: "neutral" as Tone }];
}

function mapMeta(meta: ApiMeta, selectedDate: string, productLabel: string, metrics: ApiMetric[], coverage: ApiEnvelope<unknown>["data"]["main_contract_coverage"] = []) {
  const warnings = meta.warnings ?? [];
  const updated = meta.updated_at ? new Date(meta.updated_at) : null;
  const staleHours = updated && Number.isFinite(updated.getTime())
    ? Math.max(0, Math.floor((Date.now() - updated.getTime()) / 3_600_000))
    : 0;
  const backend = meta.is_demo || meta.data_backend === "demo"
    ? "demo"
    : ["mysql", "rds"].includes(meta.data_backend ?? "") ? "rds" : "local";
  const meaningfulWarnings = warnings.filter((warning) => warning !== "demo_fixture");
  const priceGaps = coverage.filter(row => row.price_status !== "ready").length;
  const seatGaps = coverage.filter(row => row.seat_status !== "ready").length;
  const notices = [];
  if (priceGaps || seatGaps) {
    const gaps = coverage.filter(row => row.price_status !== "ready" || row.seat_status !== "ready");
    const details = gaps.slice(0, 3).map(row => `${row.product_code ?? "未知品种"}/${row.symbol ?? "主力未确定"}`).join("、");
    notices.push(`${selectedDate} 主力缺口：${[priceGaps && `价格 ${priceGaps} 个品种`, seatGaps && `席位 ${seatGaps} 个品种`].filter(Boolean).join("、")}（${details}${gaps.length > 3 ? "等" : ""}）`);
  }
  for (const warning of meaningfulWarnings) {
    const history = /^main layer pending: price=(\d+), seat=(\d+)$/.exec(warning);
    if (warning === "P2 backfill provenance unavailable; not certified") notices.push("本批次未关联上游修订凭证");
    else if (history) notices.push(`历史主力覆盖缺口：价格 ${history[1]}、席位 ${history[2]} 个品种交易日（非当日，两项可能重叠）`);
    else notices.push("其他数据校验提示");
  }
  const state = metrics.length === 0
    ? "empty"
    : notices.length > 0 ? "partial"
    : staleHours > 48 ? "stale" : "ready";
  return {
    state,
    statusLabel: `${backend === "rds" ? "RDS" : backend === "demo" ? "演示" : "数据"}批次 · ${meta.complete ? "已完成" : "未完成"}`,
    tradeDate: selectedDate,
    updatedAt: meta.updated_at ?? "未知",
    selectedProductLabel: productLabel,
    warnings,
    notice: [...new Set(notices)].join("；"),
  } as DashboardSnapshot["meta"];
}

export function mapApiSnapshot(
  filters: DashboardFilters,
  optionsEnvelope: ApiEnvelope<ApiOptions>,
  combinationEnvelope?: ApiEnvelope<ApiCombinationView>,
  marketEnvelope?: ApiEnvelope<ApiMarketView>,
  termEnvelope?: ApiEnvelope<ApiTermView>,
  validationEnvelope?: ApiEnvelope<ApiValidationView>,
): DashboardSnapshot {
  const options = optionsEnvelope.data;
  const active = combinationEnvelope ?? marketEnvelope ?? termEnvelope ?? validationEnvelope ?? optionsEnvelope;
  const selectedDate = filters.tradeDate || options.trade_date;
  const combination: ApiCombinationView = combinationEnvelope?.data ?? {
    selected_date: selectedDate, metrics: marketEnvelope?.data.metrics ?? termEnvelope?.data.metrics ?? [],
    net_position_top: [], long_top: [], short_top: [], signals: [], combinations: [], combination_signals: [],
    insight: { code: "", text: "", confidence: null }, combination_signal_product_count: 0, divergence_cards: [],
  };
  const market: ApiMarketView = marketEnvelope?.data ?? { selected_date: selectedDate, metrics: [], anomalies: [], treemap: [], signal_counts: [] };
  const term: ApiTermView = termEnvelope?.data ?? { selected_date: selectedDate, contracts: [], opposite_seats: [], min_leg_position: 300 };
  const validation = validationEnvelope?.data ?? { rules: [] };
  const percentile = (code: string) => filters.productCode === "all" ? null : validation.rules.reduce<number | null>((max, row) => row.rule_code !== code || row.current_percentile == null ? max : Math.max(max ?? row.current_percentile, row.current_percentile), null);
  const products = productOptions(options);
  const productLabel = selectedProductLabel(products, filters.productCode);
  const signals = [...combination.signals, ...market.anomalies, ...(term.signals ?? [])];
  const seatTags = tagsBySeat(signals, [
    ...combination.net_position_top,
    ...combination.long_top,
    ...combination.short_top,
    ...combination.signals,
    ...market.anomalies,
    ...term.opposite_seats,
    ...(term.top_changes ?? []),
  ]);
  return {
    meta: mapMeta(
      active.meta,
      combination.selected_date || options.trade_date,
      productLabel,
      combination.metrics,
      active.data.main_contract_coverage,
    ),
    products,
    overview: {
      hasSeatGroups: combination.metrics.some((metric) => ["consensus_long", "consensus_short", "main_long_retail_short", "main_short_retail_long"].includes(metric.divergence_state)),
      metrics: metricCards(combination.metrics),
      signalCounts: signalCountRows(market, term),
    },
    divergencePair: combination.divergence_pair?.join(" × "),
    divergenceCards: combination.divergence_cards.map((card, index) => ({
      code: card.code,
      label: card.label,
      count: card.count,
      tone: (["negative", "accent", "warning", "positive"] as Tone[])[index] ?? "neutral",
      products: card.products.map((product) => ({ code: product.product_code, label: product.display_name })),
    })),
    combinations: combinationRows(combination.combinations),
    combinationMatrix: combinationMatrixRows(combination),
    researchSummary: {
      f2Percentile: percentile("f2_position_ratio"),
      netCr5Percentile: percentile("net_cr5_concentration"),
      libraryCutoff: validationEnvelope?.data.library_cutoff ?? null,
    },
    netTop: netRows(combination.net_position_top, seatTags),
    longTop: rankingRows(combination.long_top, "多", seatTags),
    shortTop: rankingRows(combination.short_top, "空", seatTags),
    anomalies: anomalyRows(market.anomalies, seatTags),
    treemap: treemapRows(market.treemap, products),
    termStructure: {
      productLabel,
      threshold: term.min_leg_position,
      contracts: contractRows(term.contracts),
      oppositeSeats: oppositeRows(term.opposite_seats, seatTags),
      topChanges: termChangeRows(term.top_changes, seatTags),
    },
    validation: validationRows(validation.rules),
  };
}

function buildQuery(filters: DashboardFilters): string {
  const query = new URLSearchParams({ scope: filters.scope });
  if (filters.productCode && filters.productCode !== "all") query.set("product_code", filters.productCode);
  if (filters.tradeDate) query.set("trade_date", filters.tradeDate);
  return query.toString();
}

export function createApiAdapter(
  baseUrl = import.meta.env.NEXT_PUBLIC_API_BASE_URL || "/api/seats/v1",
): DashboardClientAdapter {
  const prefix = baseUrl.replace(/\/$/, "");
  const requestJson = createJsonRequester();
  let lastFilters: DashboardFilters = { productCode: "RB.SHFE", tradeDate: "", scope: "all" };
  let lastVersion: string | number | null | undefined;
  const version = (meta: ApiMeta) => meta.data_version ?? meta.batch_id;
  return {
    async loadSnapshot(filters, signal, view = "combination") {
      for (let attempt = 0; attempt < 2; attempt++) {
        const optionsRequest = requestJson<ApiEnvelope<ApiOptions>>(`${prefix}/meta/options`, { signal });
        let resolved = filters;
        if (filters.productCode !== "all" && !filters.productCode.includes(".")) {
          const options = await optionsRequest;
          const product = options.data.products.find(item => item.root.toUpperCase() === filters.productCode.toUpperCase());
          if (!product) throw new Error(`该品种暂无席位数据：${filters.productCode}`);
          resolved = { ...filters, productCode: product.product_code };
        }
        const query = buildQuery(resolved);
        const [options, combination, market, term, validation] = await Promise.all([
          optionsRequest,
          view === "combination" ? requestJson<ApiEnvelope<ApiCombinationView>>(`${prefix}/views/combination?${query}`, { signal }) : undefined,
          view === "market" ? requestJson<ApiEnvelope<ApiMarketView>>(`${prefix}/views/market-signals?${query}`, { signal }) : undefined,
          view === "term" ? requestJson<ApiEnvelope<ApiTermView>>(`${prefix}/views/term-structure?${query}`, { signal }) : undefined,
          view === "term" ? requestJson<ApiEnvelope<ApiValidationView>>(`${prefix}/views/validation?${query}&limit=1000`, { signal }) : undefined,
        ]);
        if ([combination, market, term, validation].some(result => result && version(result.meta) !== version(options.meta))) continue;
        ensureNotAborted(signal);
        lastFilters = { ...resolved, tradeDate: resolved.tradeDate || options.data.trade_date };
        lastVersion = version(options.meta);
        return mapApiSnapshot(resolved, options, combination, market, term, validation);
      }
      throw new Error("数据批次正在切换，请刷新后重试");
    },
    async loadSeatHistory(seatId, signal, productCode = lastFilters.productCode) {
      const query = new URLSearchParams({ limit: "500" });
      if (productCode && productCode !== "all") {
        query.set("product_code", productCode);
      }
      if (lastFilters.tradeDate) query.set("end_date", lastFilters.tradeDate);
      const envelope = await requestJson<ApiEnvelope<ApiHistoryPayload>>(
        `${prefix}/seats/${encodeURIComponent(seatId)}/history?${query}`,
        { signal },
      );
      if (version(envelope.meta) !== lastVersion) throw new Error("数据批次已更新，请刷新看板后再查看历史");
      const rows = [...envelope.data.rows].reverse();
      const first = rows[0];
      const points: HistoryPoint[] = rows.map((row) => ({
        date: row.trade_date,
        longPosition: row.long_position,
        shortPosition: row.short_position,
        netPosition: row.net_position,
      }));
      return {
        seatId,
        seatName: first?.seat_name ?? seatId,
        productLabel: productCode === "all" ? "全市场" : first?.product_code ?? productCode,
        points,
      };
    },
  };
}

export const defaultAdapter = createApiAdapter();
