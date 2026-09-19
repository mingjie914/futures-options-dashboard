import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as echarts from 'echarts';
import './options.css';
import SurfaceComparison from './SurfaceComparison.jsx';
import ExitLab from './ExitLab.jsx';
import { holdingRows } from './holdings.js';
import { buildCandidates, budgetGroups } from './candidates.js';

const SNAPSHOT_ENDPOINT = '/api/options/v1/snapshot';
const REFRESH_ENDPOINT = '/api/options/v1/refresh';
const SCENARIO_ENDPOINT = '/api/options/v1/scenarios';
const BASKET_STORAGE_KEY = 'options.basket.v1';
const MISSING = '—';
const MONTH_COLORS = ['#3274d2', '#8b5dc5', '#38878c', '#af8033', '#6f7ec0', '#a66587'];
const TAB_ITEMS = [
  { id: 'workspace', label: '工作台' },
  { id: 'exit', label: '退出分析' },
  { id: 'volatility', label: '波动率' },
  { id: 'holding', label: '持仓成交' },
  { id: 'chain', label: '完整期权链' },
];
const PRODUCT_OPTIONS = [
  { value: 'IM', label: 'IM · 中证1000', mapping: '期权 MO' },
  { value: 'IF', label: 'IF · 沪深300', mapping: '期权 IO' },
  { value: 'IH', label: 'IH · 上证50', mapping: '期权 HO' },
  { value: 'AU', label: 'AU · 黄金', mapping: '期权 AU' },
];
const QUALITY_LABELS = {
  ok: '正常',
  valid: '正常',
  quote_missing: '盘口缺失',
  missing_or_invalid_quote: '报价缺失或无效',
  partial_quote: '盘口不完整',
  iv_missing: 'IV缺失',
  iv_unavailable: 'IV不可用',
  greeks_missing: 'Greeks缺失',
  stale: '时效待核',
  zero_quoted: '无有效报价',
};

function toNumber(value) {
  if (value === null || value === undefined || value === '') return null;
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatNumber(value, digits = 0) {
  const number = toNumber(value);
  if (number === null) return MISSING;
  return number.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatSigned(value, digits = 0) {
  const number = toNumber(value);
  if (number === null) return MISSING;
  return `${number > 0 ? '+' : ''}${formatNumber(number, digits)}`;
}

function formatPrice(value, digits = 1) {
  return formatNumber(value, digits);
}

function formatIv(value) {
  const number = toNumber(value);
  return number === null ? MISSING : `${formatNumber(number * 100, 1)}%`;
}

function formatDelta(value) {
  return formatNumber(value, 2);
}

function formatDate(value) {
  if (value === null || value === undefined || value === '') return MISSING;
  return String(value).slice(0, 10);
}

function formatDateTime(value) {
  if (value === null || value === undefined || value === '') return MISSING;
  return String(value).replace('T', ' ').slice(0, 19);
}

function qualityLabel(value) {
  if (value === null || value === undefined || value === '') return '';
  const raw = String(value);
  return QUALITY_LABELS[raw.toLowerCase()] || raw;
}

function modeLabel(mode) {
  return mode === 'live' ? '手动盘中截面' : '归档样本';
}

function modeClass(mode) {
  return mode === 'live' ? 'options-quality-live' : 'options-quality-replay';
}

function snapshotAgeLabel(asof, mode, now = Date.now()) {
  if (mode !== 'live' || asof === null || asof === undefined || asof === '') return '';
  const timestamp = Date.parse(String(asof));
  if (!Number.isFinite(timestamp) || now - timestamp <= 30000) return '';
  return '已冻结 · 需手动刷新';
}

function monthColor(months, monthId) {
  const index = months.findIndex((month) => String(month.id) === String(monthId));
  return MONTH_COLORS[(index < 0 ? 0 : index) % MONTH_COLORS.length];
}

function ColoredCode({ code, color }) {
  if (code === null || code === undefined || code === '') {
    return <span className="options-code">{MISSING}</span>;
  }
  const text = String(code);
  const digitIndex = text.search(/\d/);
  if (digitIndex < 0) return <span className="options-code">{text}</span>;
  return (
    <span className="options-code">
      {text.slice(0, digitIndex)}
      <span style={{ color }}>{text.slice(digitIndex)}</span>
    </span>
  );
}

function normalizeBasketValue(value) {
  if (typeof value === 'string') {
    return { code: value, side: 'buy', quantity: 1 };
  }
  if (!value || typeof value !== 'object' || typeof value.code !== 'string' || !value.code.trim()) {
    return null;
  }
  const quantityValue = toNumber(value.quantity);
  return {
    code: value.code.trim(),
    side: value.side === 'sell' ? 'sell' : 'buy',
    quantity: Math.max(1, Math.min(9999, Math.trunc(quantityValue === null ? 1 : quantityValue))),
  };
}

function readBasket() {
  if (typeof window === 'undefined') return [];
  try {
    const raw = window.localStorage.getItem(BASKET_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.map(normalizeBasketValue).filter(Boolean);
  } catch {
    return [];
  }
}

function normalizeSnapshot(value) {
  const raw = value && typeof value === 'object' ? value : {};
  const months = Array.isArray(raw.months)
    ? raw.months.filter(Boolean).map((month) => ({ ...month, id: String(month.id ?? '') }))
    : [];
  const rows = Array.isArray(raw.rows)
    ? raw.rows.filter(Boolean).map((row) => ({ ...row, month: String(row.month ?? '') }))
    : [];
  return {
    ...raw,
    mode: raw.mode === 'live' ? 'live' : 'replay',
    months,
    rows,
    warnings: Array.isArray(raw.warnings) ? raw.warnings : [],
    coverage: raw.coverage && typeof raw.coverage === 'object' ? raw.coverage : {},
    rates: raw.rates && typeof raw.rates === 'object' ? raw.rates : {},
  };
}

async function responseError(response, fallback) {
  let detail = '';
  try {
    const payload = await response.clone().json();
    const candidate = payload?.detail ?? payload?.message ?? payload?.error;
    if (Array.isArray(candidate)) {
      detail = candidate.map((item) => typeof item === 'string' ? item : item?.msg || item?.message || JSON.stringify(item)).join('；');
    } else if (candidate && typeof candidate === 'object') {
      detail = candidate.message || candidate.msg || JSON.stringify(candidate);
    } else if (candidate !== null && candidate !== undefined) {
      detail = String(candidate);
    }
  } catch {
    try {
      const text = await response.clone().text();
      if (text.trim()) detail = text.trim().slice(0, 280);
    } catch {
      // The HTTP status is still useful when a response has no readable body.
    }
  }
  return `${fallback}${detail ? `：${detail}` : `（HTTP ${response.status}）`}`;
}

function getMonthRows(rows, monthId) {
  return (Array.isArray(rows) ? rows : []).filter((row) => String(row.month) === String(monthId));
}

function getRowsByCode(rows) {
  return new Map((Array.isArray(rows) ? rows : []).filter((row) => row.code).map((row) => [String(row.code), row]));
}

function hasQuote(row) {
  if (!row) return false;
  return ['bid', 'ask', 'last', 'close'].some((field) => toNumber(row[field]) !== null);
}

function uniqueStrikes(rows) {
  return [...new Set((Array.isArray(rows) ? rows : [])
    .map((row) => toNumber(row.strike))
    .filter((strike) => strike !== null))].sort((left, right) => right - left);
}

function nearestStrikes(strikes, spot, count = 3) {
  const value = toNumber(spot);
  if (value === null) return new Set();
  return new Set([...strikes].sort((left, right) => Math.abs(left - value) - Math.abs(right - value)).slice(0, count));
}

function groupRowsByStrike(rows) {
  const groups = new Map();
  (Array.isArray(rows) ? rows : []).forEach((row) => {
    const strike = toNumber(row.strike);
    if (strike === null || !['C', 'P'].includes(row.cp)) return;
    if (!groups.has(strike)) groups.set(strike, {});
    groups.get(strike)[row.cp] = row;
  });
  return groups;
}

function EmptyState({ title, detail }) {
  return (
    <div className="options-empty">
      <h2>{title}</h2>
      {detail ? <p>{detail}</p> : null}
    </div>
  );
}

function MonthStrip({ months, selectedMonthId, onSelect }) {
  return (
    <div className="options-months" aria-label="到期月份">
      {months.map((month, index) => {
        const color = MONTH_COLORS[index % MONTH_COLORS.length];
        const contracts = toNumber(month.contracts);
        const quoted = toNumber(month.quoted);
        return (
          <button
            className={`options-month ${String(month.id) === String(selectedMonthId) ? 'options-month-selected' : ''}`}
            key={month.id || index}
            onClick={() => onSelect(month.id)}
            style={{ borderTopColor: color }}
            type="button"
          >
            <strong><ColoredCode code={month.code || `MO${String(month.id).slice(-4)}`} color={color} /></strong>
            <small>{formatDate(month.expiry)} 到期</small>
            <em>剩余 {formatNumber(month.calendar_days)} 自然日</em>
            <span className={quoted === 0 ? 'options-month-quality options-quality-warning' : 'options-month-quality'}>
              {quoted === null || contracts === null ? `${formatNumber(quoted)}/${formatNumber(contracts)} 报价` : `${formatNumber(quoted)}/${formatNumber(contracts)} 报价`}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function BasketBar({ basket, rowsByCode, months, onRemove }) {
  return (
    <section className="options-basket" aria-label="比较篮">
      <strong className="options-basket-title">比较篮 <span>· {basket.length}</span></strong>
      <div className="options-chips">
        {basket.length ? basket.map((leg, index) => {
          const row = rowsByCode.get(leg.code);
          const color = monthColor(months, row?.month);
          return (
            <span className="options-chip" key={`${leg.code}-${index}`}>
              <button type="button" onClick={() => onRemove(index)} title="移出比较篮">
                <ColoredCode code={leg.code} color={color} />
                <span className={leg.side === 'buy' ? 'options-chip-buy' : 'options-chip-sell'}>{leg.side === 'buy' ? '买' : '卖'}{leg.quantity}</span>
                <span aria-hidden="true">×</span>
              </button>
            </span>
          );
        }) : <span className="options-basket-empty">从上方候选策略一键载入，也可在期权链自选。</span>}
      </div>
      {basket.length ? (
        <div className="options-basket-note">仅用于情景计算，不会提交交易。</div>
      ) : null}
    </section>
  );
}

function LegRow({ leg, index, row, months, onChange, onRemove }) {
  const color = monthColor(months, row?.month);
  return (
    <div className="options-leg-row">
      <div className="options-leg-contract">
        <span className="options-leg-index">{index + 1}</span>
        <div>
          <strong><ColoredCode code={leg.code} color={color} /></strong>
          <small>{row ? `${row.cp === 'C' ? 'Call' : 'Put'} · ${formatNumber(row.strike)}` : '合约明细待覆盖'}</small>
        </div>
      </div>
      <select aria-label={`${leg.code} 方向`} value={leg.side} onChange={(event) => onChange(index, { side: event.target.value })}>
        <option value="buy">买入</option>
        <option value="sell">卖出</option>
      </select>
      <label className="options-quantity">
        <span>手数</span>
        <input
          aria-label={`${leg.code} 手数`}
          min="1"
          max="9999"
          onChange={(event) => onChange(index, { quantity: event.target.value })}
          type="number"
          value={leg.quantity}
        />
      </label>
      <button className="options-icon-button" type="button" onClick={() => onRemove(index)} aria-label={`移除 ${leg.code}`}>×</button>
    </div>
  );
}

function ScenarioResult({ state }) {
  if (state.status === 'idle') {
    return <p className="options-note">提交后由后端根据当前快照计算，未覆盖的结果保持为{MISSING}。</p>;
  }
  if (state.status === 'loading') {
    return <div className="options-inline-status">正在计算情景…</div>;
  }
  if (state.status === 'error') {
    return <div className="options-inline-error">情景计算失败：{state.error}</div>;
  }
  const result = state.result || {};
  const maxLoss = toNumber(result.max_loss) !== null
    ? formatNumber(result.max_loss, 2)
    : result.risk_status === 'same_expiry'
      ? '无上限'
      : result.risk_status === 'cross_expiry_unresolved'
        ? '待处置确认'
        : MISSING;
  return (
    <>
      <div className="options-scenario-values">
        {[
          ['成本', result.cost],
          ['最大亏损', maxLoss],
          ['最大收益', result.max_gain === null && result.risk_status === 'same_expiry' ? '无上限' : result.max_gain],
          ['情景损益', result.pnl],
        ].map(([label, value]) => (
          <div className="options-scenario-value" key={label}>
            <span>{label}</span>
            <strong>{typeof value === 'string' ? value : formatNumber(value, 2)}</strong>
          </div>
        ))}
      </div>
      <div className="options-result-meta">
        费用计入：{result.fees_included === true ? '是' : result.fees_included === false ? '否' : MISSING}
        {result.risk_status === 'same_expiry' ? ' · 最大损益按同一到期日计算' : ''}
        {result.risk_status === 'cross_expiry_unresolved' ? ' · 风险范围：跨到期日，待处置确认' : ''}
        {Array.isArray(result.warnings) && result.warnings.length ? ` · ${result.warnings.join('；')}` : ''}
      </div>
    </>
  );
}

function CandidateComparison({ snapshot, month, target, days, ivShift, onLoad, months }) {
  const [filter, setFilter] = useState('all');
  const [comparison, setComparison] = useState('unit');
  const [budget, setBudget] = useState('20000');
  const [spreadLimit, setSpreadLimit] = useState('20');
  const [minSize, setMinSize] = useState('1');
  const [results, setResults] = useState({});
  const candidates = useMemo(() => buildCandidates(snapshot.rows, month?.id, toNumber(snapshot.spot), toNumber(target), Number(spreadLimit) / 100, Number(minSize)), [snapshot, month?.id, target, spreadLimit, minSize]);
  const requestKey = JSON.stringify([snapshot.snapshot_id, month?.id, target, days, ivShift, spreadLimit, minSize]);
  useEffect(() => {
    const controller = new AbortController();
    setResults({});
    const timer = setTimeout(() => {
      if (toNumber(target) === null || Number(target) <= 0 || toNumber(days) === null || Number(days) < 0 || toNumber(ivShift) === null) return;
      candidates.forEach(async (candidate) => {
        try {
          const response = await fetch(SCENARIO_ENDPOINT, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: controller.signal,
            body: JSON.stringify({ snapshot_id: snapshot.snapshot_id, legs: candidate.legs, target: Number(target), days: Number(days), iv_shift_pp: Number(ivShift) }),
          });
          if (!response.ok) throw new Error(await responseError(response, '计算失败'));
          const result = await response.json();
          if (!controller.signal.aborted) setResults(current => ({ ...current, [candidate.id]: { key: requestKey, result } }));
        } catch (error) {
          if (!controller.signal.aborted) setResults(current => ({ ...current, [candidate.id]: { key: requestKey, error: error.message } }));
        }
      });
    }, 300);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [candidates, requestKey, snapshot.snapshot_id, target, days, ivShift]);
  return <section className="options-surface options-candidates">
    <div className="options-heading"><div><h2>看多候选策略</h2><p className="options-muted">按当前月份与真实盘口生成 · 按下方基准比较 · 目标 {formatNumber(target)} / {days} 日历天 / IV {formatSigned(ivShift, 1)} 个百分点</p></div>
      <div className="options-segment">{[['all', '全部'], ['buyer', '纯买方'], ['spread', '价差']].map(([value, label]) => <button key={value} className={filter === value ? 'options-segment-on' : ''} onClick={() => setFilter(value)} type="button">{label}</button>)}</div>
    </div>
    <div className="options-analysis-tools">
      <label>比较基准<select value={comparison} onChange={e => setComparison(e.target.value)}><option value="unit">每组 1 手</option><option value="budget">同等亏损预算</option></select></label>
      {comparison === 'budget' ? <label>最大亏损预算（元）<input type="number" min="1" value={budget} onChange={e => setBudget(e.target.value)} /></label> : null}
      <label>价差率上限<select value={spreadLimit} onChange={e => setSpreadLimit(e.target.value)}><option value="10">10%</option><option value="20">20%</option><option value="30">30%</option></select></label>
      <label>双边盘口至少<select value={minSize} onChange={e => setMinSize(e.target.value)}><option value="1">1 手</option><option value="5">5 手</option><option value="10">10 手</option></select></label>
    </div>
    <div className="options-candidate-grid">{candidates.filter(c => filter === 'all' || c.kind === filter).map(candidate => {
      const state = results[candidate.id]?.key === requestKey ? results[candidate.id] : null;
      const unit = state?.result;
      const groups = comparison === 'budget' ? budgetGroups(unit, candidate, snapshot.rows, Number(budget)) : 1;
      const result = unit && groups > 0 ? { ...unit, ...Object.fromEntries(['cost', 'max_loss', 'max_gain', 'pnl'].map(k => [k, unit[k] === null ? null : unit[k] * groups])), legs: unit.legs?.map(leg => ({ ...leg, pnl: leg.pnl * groups })) } : null;
      const selectedLegs = candidate.legs.map(leg => ({ ...leg, quantity: leg.quantity * groups }));
      return <article className="options-candidate-card" key={candidate.id}>
        <h3>{candidate.name}</h3><p className="options-muted">{candidate.description}</p>
        <div className="options-candidate-legs">{candidate.legs.map(leg => <div key={leg.code}><span>{leg.side === 'buy' ? '买' : '卖'} {groups || '—'} 手 </span><ColoredCode code={leg.code} color={monthColor(months, month?.id)} /></div>)}</div>
        <dl>{[['净投入', result?.cost], ['最大亏损', result?.max_loss], ['到期最大收益', result?.max_gain === null && result ? '无上限' : result?.max_gain], ['目标情景损益', result?.pnl]].map(([label, value]) => <div key={label}><dt>{label}（元）</dt><dd className={label === '目标情景损益' && toNumber(value) !== null ? (Number(value) >= 0 ? 'options-up' : 'options-down') : ''}>{typeof value === 'string' ? value : formatNumber(value, 0)}</dd></div>)}</dl>
        {comparison === 'budget' && unit ? <p className="options-note">{groups > 0 ? `${groups} 组 · 实际最大亏损 ${formatNumber(result.max_loss)} / 预算 ${formatNumber(budget)} 元` : '预算或当前盘口不足以支持一组，未缩放为零风险候选。'}</p> : null}
        {state?.error ? <p className="options-inline-error">{state.error}</p> : !result ? <p className="options-note">填写有效情景参数后自动计算</p> : null}
        <button className="options-primary-button" type="button" onClick={() => onLoad(selectedLegs, result)} disabled={!result}>一键载入{candidate.legs.length > 1 ? '组合' : '合约'}</button>
      </article>;
    })}</div>
    {!candidates.length ? <p className="options-note">当前月份没有符合价差率和盘口量条件的合约；可调整筛选条件，不会补充示例报价。</p> : null}
    <p className="options-note">候选是规则模板，不是收益排名。预算模式按整数组合、当前可见盘口计算；盘口不保证成交。最大损益按到期计算；目标情景为提前退出模型估值，沿用当前价差，未扣手续费。一键载入会替换当前比较篮。</p>
  </section>;
}

function WorkspaceTab({ snapshot, month, onLoad, basket, rowsByCode, months, target, days, ivShift, scenario, onTarget, onDays, onIvShift, onCalculate, onLegChange, onLegRemove }) {
  return (
    <div className="options-tab-panel options-workspace-panel">
      <CandidateComparison snapshot={snapshot} month={month} months={months} target={target} days={days} ivShift={ivShift} onLoad={onLoad} />
      <div className="options-workspace-grid">
        <section className="options-surface">
          <div className="options-heading">
            <div>
              <h2>比较篮</h2>
              <p className="options-muted">选择方向与手数，组合只保存在本机。</p>
            </div>
            <span className="options-section-tag">{basket.length} 个合约</span>
          </div>
          {basket.length ? basket.map((leg, index) => (
            <LegRow
              index={index}
              key={`${leg.code}-${index}`}
              leg={leg}
              months={months}
              onChange={onLegChange}
              onRemove={onLegRemove}
              row={rowsByCode.get(leg.code)}
            />
          )) : <p className="options-note">点击上方候选的“一键载入”，即可调整合约手数和组合方向。</p>}
        </section>
        <section className="options-surface">
          <div className="options-heading">
            <div>
              <h2>情景参数</h2>
              <p className="options-muted">调整目标、持有时间和 IV，候选策略将自动重新比较。</p>
            </div>
          </div>
          <div className="options-form-grid">
            <label>目标指数点位<input inputMode="decimal" min="0" onChange={(event) => onTarget(event.target.value)} type="number" value={target} /></label>
            <label>截面后日历天数<input inputMode="numeric" min="0" step="1" onChange={(event) => onDays(event.target.value)} type="number" value={days} /></label>
            <label>IV 平移（百分点）<input inputMode="decimal" step="0.1" onChange={(event) => onIvShift(event.target.value)} type="number" value={ivShift} /></label>
          </div>
          <p className="options-time-context">从 {formatDateTime(snapshot.asof)} 起算 → 预计退出 {scenarioExitTime(snapshot.asof, days)}（上海时间）</p>
          <button className="options-plain-button" type="button" onClick={() => onDays(String((snapshot.rows.find(row => String(row.month) === String(month.id))?.t ?? 0) * 365))}>至所选月到期</button>
          <button className="options-primary-button" disabled={!basket.length || scenario.status === 'loading'} onClick={onCalculate} type="button">
            {scenario.status === 'loading' ? '计算中…' : '计算情景'}
          </button>
          <p className="options-note">按买入付卖价、卖出收买价估算成本，未扣费用。到期模式的目标指结算标的；提前退出沿用当前基差。新用户目标预设为截面指数约 +1%，可自行修改。</p>
        </section>
      </div>
      <section className="options-surface options-scenario-surface">
        <div className="options-heading">
          <div>
            <h2>情景结果</h2>
            <p className="options-muted">基于同一行情截面的一次计算。</p>
          </div>
        </div>
        <ScenarioResult state={scenario} />
      </section>
    </div>
  );
}

function VolatilityTab({ rows, month, months, spot }) {
  const [view, setView] = useState('single');
  const chartRef = useRef(null);
  const monthRows = useMemo(() => getMonthRows(rows, month?.id), [rows, month?.id]);
  const callPoints = useMemo(() => monthRows
    .filter((row) => row.cp === 'C' && toNumber(row.strike) !== null && toNumber(row.iv) !== null)
    .map((row) => [toNumber(row.strike), toNumber(row.iv) * 100])
    .sort((left, right) => left[0] - right[0]), [monthRows]);
  const putPoints = useMemo(() => monthRows
    .filter((row) => row.cp === 'P' && toNumber(row.strike) !== null && toNumber(row.iv) !== null)
    .map((row) => [toNumber(row.strike), toNumber(row.iv) * 100])
    .sort((left, right) => left[0] - right[0]), [monthRows]);
  const nearestRows = useMemo(() => {
    const value = toNumber(spot);
    return monthRows
      .filter((row) => toNumber(row.strike) !== null)
      .sort((left, right) => Math.abs(toNumber(left.strike) - (value ?? 0)) - Math.abs(toNumber(right.strike) - (value ?? 0)))
      .slice(0, 6);
  }, [monthRows, spot]);

  useEffect(() => {
    const node = chartRef.current;
    if (!node) return undefined;
    const chart = echarts.init(node);
    const spotValue = toNumber(spot);
    chart.setOption({
      animation: false,
      color: ['#3274d2', '#a66587'],
      grid: { left: 48, right: 22, top: 44, bottom: 42 },
      legend: { top: 6, right: 8, itemWidth: 14, itemHeight: 8, textStyle: { color: '#6d7c90', fontSize: 12 } },
      tooltip: {
        trigger: 'axis',
        formatter: (params) => {
          const items = Array.isArray(params) ? params : [params];
          const strike = items[0]?.value?.[0];
          return [`执行价 ${formatNumber(strike)}`, ...items.map((item) => `${item.seriesName} ${formatNumber(item.value?.[1], 1)}%`)].join('<br/>');
        },
      },
      xAxis: {
        type: 'value',
        scale: true,
        min: 'dataMin',
        max: 'dataMax',
        name: '执行价',
        nameLocation: 'middle',
        nameGap: 28,
        axisLabel: { color: '#7e8b9c', fontSize: 11 },
        axisLine: { lineStyle: { color: '#d9e0e9' } },
        splitLine: { lineStyle: { color: '#edf0f5' } },
      },
      yAxis: {
        type: 'value',
        name: 'IV (%)',
        nameTextStyle: { color: '#7e8b9c', fontSize: 11 },
        axisLabel: { color: '#7e8b9c', fontSize: 11, formatter: (value) => `${value}%` },
        axisLine: { show: false },
        splitLine: { lineStyle: { color: '#edf0f5' } },
      },
      series: [
        { name: 'Call IV', type: 'line', data: callPoints, showSymbol: true, symbolSize: 7, connectNulls: false, lineStyle: { width: 2 } },
        { name: 'Put IV', type: 'line', data: putPoints, showSymbol: true, symbolSize: 7, connectNulls: false, lineStyle: { width: 2 } },
      ],
    });
    if (spotValue !== null) {
      chart.setOption({
        series: [
          { markLine: { silent: true, symbol: 'none', lineStyle: { color: '#6f7ec0', type: 'dashed' }, label: { formatter: '现货', color: '#6f7ec0' }, data: [{ xAxis: spotValue }] } },
          { markLine: { silent: true, data: [] } },
        ],
      });
    }
    const resize = () => chart.resize();
    window.addEventListener('resize', resize);
    let observer;
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(resize);
      observer.observe(node);
    }
    return () => {
      window.removeEventListener('resize', resize);
      observer?.disconnect();
      chart.dispose();
    };
  }, [callPoints, putPoints, spot, view]);

  const hasPoints = callPoints.length || putPoints.length;
  const nearestSet = useMemo(() => nearestStrikes(uniqueStrikes(monthRows), spot, 3), [monthRows, spot]);
  return (
    <div className="options-tab-panel">
      <div className="options-heading">
        <div>
          <h2>波动率微笑</h2>
          <p className="options-muted"><ColoredCode code={month?.code || month?.id} color={monthColor(months, month?.id)} /> · 仅绘制实际快照 IV 点位</p>
        </div>
        <span className="options-section-tag">{formatNumber(callPoints.length + putPoints.length)} 个 IV 点</span>
      </div>
      <div className="options-segment options-analysis-tools">{[['single','单月微笑'],['smiles','跨月微笑'],['term','ATM 期限结构'],['skew','25Δ 偏度']].map(([id,label])=><button key={id} type="button" className={view===id?'options-segment-on':''} onClick={()=>setView(id)}>{label}</button>)}</div>
      {view !== 'single' ? <SurfaceComparison rows={rows} months={months} metric={view} /> : <section className="options-surface options-chart-surface">
        {hasPoints ? <div className="options-vol-chart" ref={chartRef} aria-label="实际 IV 微笑图" /> : <EmptyState title="当前月份没有可绘制 IV" detail="缺失值保持为—，不会补值。" />}
      </section>}
      <section className="options-surface">
        <div className="options-heading options-heading-tight">
          <div>
            <h3>现货附近合约</h3>
            <p className="options-muted">现货附近的原始报价 IV；跨月指标见上方比较视图。</p>
          </div>
          <span className="options-muted">现货 {formatPrice(spot)}</span>
        </div>
        {nearestRows.length ? (
          <div className="options-mini-table-wrap">
            <table className="options-mini-table">
              <thead><tr><th>合约</th><th>执行价</th><th>方向</th><th>IV</th><th>IV买</th><th>IV卖</th><th>质检</th></tr></thead>
              <tbody>{nearestRows.map((row) => (
                <tr className={nearestSet.has(toNumber(row.strike)) ? 'options-near-row' : ''} key={`${row.code}-${row.cp}`}>
                  <td><ColoredCode code={row.code} color={monthColor(months, row.month)} /></td>
                  <td>{formatNumber(row.strike)}</td>
                  <td>{row.cp === 'C' ? 'Call' : 'Put'}</td>
                  <td>{formatIv(row.iv)}</td>
                  <td>{formatIv(row.iv_bid)}</td>
                  <td>{formatIv(row.iv_ask)}</td>
                  <td>{qualityLabel(row.quality) || MISSING}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        ) : <EmptyState title="没有执行价行" />}
      </section>
    </div>
  );
}

function holdingSegments(current, change) {
  if (current === null) return { base: null, added: null, removed: null, extent: null, invalidBaseline: false };
  if (change === null) return { base: current, added: null, removed: null, extent: current, invalidBaseline: false };
  const impliedBaseline = current - change;
  const invalidBaseline = impliedBaseline < 0;
  return {
    base: invalidBaseline ? 0 : Math.max(current - Math.max(change, 0), 0),
    added: Math.max(change, 0),
    removed: Math.max(-change, 0),
    extent: invalidBaseline ? Math.max(current, change) : current + Math.max(-change, 0),
    invalidBaseline,
  };
}

function HoldingBar({ row, side, metric, scale }) {
  const current = row ? toNumber(metric === 'oi' ? row.oi : row.volume) : null;
  const change = metric === 'oi' && row ? toNumber(row.oi_change) : null;
  const parts = holdingSegments(current, change);
  if (current === null) return <span className="options-bar-missing">{MISSING}</span>;
  const width = (value) => `${Math.max(0, Math.min(100, ((value || 0) / scale) * 100))}%`;
  const title = metric === 'oi'
    ? `当前持仓 ${formatNumber(current)} 手；所选基准变化 ${formatSigned(change)} 手${parts.invalidBaseline ? '；基准推导为负，需核查' : ''}`
    : `成交量 ${formatNumber(current)} 手`;
  return (
    <div className={`options-bar-content ${side === 'call' ? 'options-bar-call' : 'options-bar-put'}`}>
      {side === 'call' ? <span className="options-bar-value">{formatNumber(current)}<small className={change !== null && change < 0 ? 'options-down' : 'options-up'}>{metric === 'oi' ? formatSigned(change) : ''}{parts.invalidBaseline ? <span className="options-bar-warning" title="前一交易日基准推导为负"> ⚠</span> : null}</small></span> : null}
      <span aria-label={title} className={`options-oi-track ${side === 'call' ? 'options-call-track' : 'options-put-track'}`} title={title}>
        {metric === 'oi' && change !== null ? <>
          <span className="options-oi-segment options-oi-base" style={{ width: width(parts.base) }} />
          <span className="options-oi-segment options-oi-added" style={{ width: width(parts.added) }} />
          <span className="options-oi-segment options-oi-removed" style={{ width: width(parts.removed) }} />
        </> : <span className="options-oi-segment options-oi-base" style={{ width: width(current) }} />}
      </span>
      {side === 'put' ? <span className="options-bar-value">{formatNumber(current)}<small className={change !== null && change < 0 ? 'options-down' : 'options-up'}>{metric === 'oi' ? formatSigned(change) : ''}{parts.invalidBaseline ? <span className="options-bar-warning" title="前一交易日基准推导为负"> ⚠</span> : null}</small></span> : null}
    </div>
  );
}

function HoldingTab({ snapshot, rows, month, months, spot, metric, onMetric, onOpenChain }) {
  const [basis, setBasis] = useState('daily');
  const [previous, setPrevious] = useState(null);
  const [previousMessage, setPreviousMessage] = useState('正在读取上一截面…');
  useEffect(() => {
    const controller = new AbortController();
    setPrevious(null); setBasis('daily'); setPreviousMessage('正在读取上一截面…');
    fetch('/api/options/v1/previous', {signal:controller.signal}).then(async response => {
      if (!response.ok) throw new Error(await responseError(response, '上一截面读取失败'));
      return response.json();
    }).then(value => {
      if (!controller.signal.aborted) {
        setPrevious(value.snapshot);
        setPreviousMessage(value.snapshot ? (value.snapshot.rows.some(row => Number.isFinite(row.oi) || Number.isFinite(row.volume)) ? '' : '上一截面未采集有效实时量仓，暂不能比较区间变化') : (value.reason || '暂无同日更早截面'));
      }
    }).catch(error => { if (!controller.signal.aborted) setPreviousMessage(error.message); });
    return () => controller.abort();
  }, [snapshot.snapshot_id]);
  const comparable = previous?.trade_date === snapshot.trade_date && Date.parse(previous.asof) < Date.parse(snapshot.asof) && previous.rows.some(row => Number.isFinite(row.oi) || Number.isFinite(row.volume));
  const basisLabel = basis === 'snapshot' && comparable ? `较上一截面 ${formatDateTime(previous.asof)}` : '较前一交易日持仓';
  const monthRows = useMemo(() => holdingRows(getMonthRows(rows, month?.id), comparable ? previous : null, basis), [rows, month?.id, previous, basis, comparable]);
  const rankings = [
    ['增仓最多', monthRows.filter(r => toNumber(r.oi_change) > 0).sort((a,b) => b.oi_change-a.oi_change).slice(0,3), 'oi_change'],
    ['减仓最多', monthRows.filter(r => toNumber(r.oi_change) !== null && r.oi_change < 0).sort((a,b) => a.oi_change-b.oi_change).slice(0,3), 'oi_change'],
    [basis === 'snapshot' ? '区间成交最多' : '当日成交最多', monthRows.filter(r => toNumber(r.volume) > 0).sort((a,b) => b.volume-a.volume).slice(0,3), 'volume'],
  ];
  const groups = useMemo(() => groupRowsByStrike(monthRows), [monthRows]);
  const strikes = useMemo(() => uniqueStrikes(monthRows), [monthRows]);
  const scale = useMemo(() => {
    const values = monthRows.map((row) => {
      const current = toNumber(metric === 'oi' ? row.oi : row.volume);
      const change = metric === 'oi' ? toNumber(row.oi_change) : null;
      return holdingSegments(current, change).extent;
    }).filter((value) => value !== null && value > 0);
    return Math.max(1, ...values);
  }, [metric, monthRows]);
  const nearSet = useMemo(() => nearestStrikes(strikes, spot, 2), [strikes, spot]);

  return (
    <div className="options-tab-panel">
      <div className="options-heading">
        <div>
          <h2>持仓分布与增减</h2>
          <p className="options-muted"><ColoredCode code={month?.code || month?.id} color={monthColor(months, month?.id)} /> · 执行价由高到低</p>
        </div>
        <div className="options-heading-actions">
          <div className="options-segment">
            {['oi', 'volume'].map((value) => <button className={metric === value ? 'options-segment-on' : ''} key={value} onClick={() => onMetric(value)} type="button">{value === 'oi' ? '持仓量 + 增减' : '成交量'}</button>)}
          </div>
          <button className="options-plain-button" onClick={onOpenChain} type="button">打开完整期权链</button>
        </div>
      </div>
      <div className="options-analysis-tools"><label>变化基准<select value={basis} onChange={e => setBasis(e.target.value)}><option value="daily">前一交易日</option><option value="snapshot" disabled={!comparable}>同日上一截面</option></select></label><span className="options-muted">{basisLabel}{previousMessage ? ` · ${previousMessage}` : ''}</span></div>
      <div className="options-ranking-grid">{rankings.map(([title, items, field]) => <section className="options-surface" key={title}><h3>{title}</h3>{items.length ? items.map(row => <div className="options-rank-row" key={row.code}><ColoredCode code={row.code} color={monthColor(months,row.month)}/><strong className={field === 'oi_change' ? (row[field] > 0 ? 'options-up' : 'options-down') : ''}>{field === 'oi_change' ? formatSigned(row[field]) : formatNumber(row[field])} 手</strong></div>) : <p className="options-note">没有可比较的有效变化</p>}</section>)}</div>
      <div className="options-holding-layout">
        <section className="options-surface">
          <div className="options-heading options-heading-tight">
            <div>
              <h3>执行价分布 <span className="options-muted options-small">单位：手</span></h3>
              <p className="options-holding-explain">{metric === 'oi' ? `实心总长 = 当前持仓；红段包含在内，绿色虚线段位于外侧。${basisLabel}。` : (basis === 'snapshot' ? '成交量为两次截面之间的增量；负修订或缺失值显示为—。' : '成交量为该行情截面的当日累计值；缺失值保持为—。')}</p>
            </div>
            <div className="options-legend"><span><i className="options-dot options-dot-blue" />存量</span><span><i className="options-dot options-dot-red" />增仓</span><span><i className="options-dot options-dot-green" />减仓</span></div>
          </div>
          <div className="options-chart-head"><span>看涨期权 Call</span><span>执行价</span><span>看跌期权 Put</span></div>
          {strikes.length ? strikes.map((strike) => {
            const group = groups.get(strike) || {};
            return (
              <div className={`options-bar-row ${nearSet.has(strike) ? 'options-near-row' : ''}`} key={strike}>
                <div className="options-bar-cell options-bar-cell-left"><HoldingBar row={group.C} side="call" metric={metric} scale={scale} /></div>
                <div className="options-strike-label">{formatNumber(strike)}</div>
                <div className="options-bar-cell"><HoldingBar row={group.P} side="put" metric={metric} scale={scale} /></div>
              </div>
            );
          }) : <EmptyState title="当前月份没有执行价数据" />}
          <div className="options-spot-line">── 现货 {formatPrice(spot)} · 平值附近仅用于视觉定位 ──</div>
        </section>
        <section className="options-surface options-holding-notes">
          <h3>读图口径</h3>
          <div className="options-keyrow"><strong>当前</strong><p>实心总长为当前 OI；增仓时由蓝段与红段共同组成。</p></div>
          <div className="options-keyrow"><strong>增仓</strong><p>红段向当前柱内叠加，变化来自接口字段。</p></div>
          <div className="options-keyrow"><strong>减仓</strong><p>绿色虚线段放在柱外，避免把历史基准藏在总长里。</p></div>
          <p className="options-note">OI 集中区仅说明持仓分布，不能单独推断卖方身份、Gamma 或方向。</p>
        </section>
      </div>
    </div>
  );
}

function ChainCell({ value, formatter, className = '', title }) {
  return <td className={className} title={title}>{formatter ? formatter(value) : formatNumber(value)}</td>;
}

function ChainTab({ rows, month, months, spot, onAdd, onSelectMonth }) {
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('all');
  const monthRows = useMemo(() => getMonthRows(rows, month?.id), [rows, month?.id]);
  const groups = useMemo(() => groupRowsByStrike(monthRows), [monthRows]);
  const strikes = useMemo(() => uniqueStrikes(monthRows), [monthRows]);
  const nearSet = useMemo(() => nearestStrikes(strikes, spot, 3), [strikes, spot]);
  const visibleStrikes = strikes.filter((strike) => {
    const group = groups.get(strike) || {};
    const textMatch = !search.trim() || String(strike).includes(search.trim()) || [group.C?.code, group.P?.code].some((code) => String(code || '').includes(search.trim()));
    const quoted = hasQuote(group.C) || hasQuote(group.P);
    const filterMatch = filter === 'all' || (filter === 'quoted' && quoted) || (filter === 'near' && nearSet.has(strike));
    return textMatch && filterMatch;
  });
  const code = month?.code || (month?.id ? `MO${String(month.id).slice(-4)}` : MISSING);
  const color = monthColor(months, month?.id);
  return (
    <div className="options-tab-panel">
      <div className="options-heading">
        <div>
          <h2>完整期权链</h2>
          <p className="options-muted"><ColoredCode code={code} color={color} /> · 买价 / 卖价 · Delta · IV · OI</p>
        </div>
        <label className="options-month-select">链月份
          <select value={month?.id || ''} onChange={(event) => onSelectMonth(event.target.value)}>
            {months.map((item, index) => <option key={item.id || index} value={item.id}>{item.code || item.id}</option>)}
          </select>
        </label>
      </div>
      <div className="options-chain-tools">
        <input aria-label="搜索执行价或合约" onChange={(event) => setSearch(event.target.value)} placeholder="搜索执行价，如 7700" type="search" value={search} />
        <div className="options-segment" aria-label="期权链筛选">
          {[['all', '全部'], ['quoted', '有报价'], ['near', '现货附近']].map(([value, label]) => <button className={filter === value ? 'options-segment-on' : ''} key={value} onClick={() => setFilter(value)} type="button">{label}</button>)}
        </div>
        <span className="options-tool-note">报价：点 · 持仓：手 · IV：%</span>
      </div>
      <section className="options-chain-wrap">
        <table className="options-chain-table">
          <colgroup><col span={13} style={{ width: `${100 / 13}%` }} /></colgroup>
          <thead>
            <tr className="options-chain-group-head">
              <th colSpan="6"><span className="options-call-label">CALL · 看涨期权</span> <ColoredCode code={code} color={color} /></th>
              <th className="options-strike-head">执行价</th>
              <th colSpan="6"><span className="options-put-label">PUT · 看跌期权</span> <ColoredCode code={code} color={color} /></th>
            </tr>
            <tr>
              <th>加入</th><th>OI</th><th>IV</th><th>Delta</th><th>卖价</th><th>买价</th>
              <th className="options-strike-head">高 → 低</th>
              <th>买价</th><th>卖价</th><th>Delta</th><th>IV</th><th>OI</th><th>加入</th>
            </tr>
          </thead>
          <tbody>
            {visibleStrikes.map((strike) => {
              const group = groups.get(strike) || {};
              return (
                <tr className={nearSet.has(strike) ? 'options-near-row' : ''} key={strike}>
                  <td><button className="options-add-button" disabled={!group.C?.code} onClick={() => onAdd(group.C.code)} type="button" aria-label={`加入 Call ${strike}`}>+</button></td>
                  <ChainCell value={group.C?.oi} />
                  <ChainCell formatter={formatIv} value={group.C?.iv} />
                  <ChainCell formatter={formatDelta} value={group.C?.delta} />
                  <ChainCell formatter={formatPrice} title={group.C?.code} value={group.C?.ask} />
                  <ChainCell formatter={formatPrice} title={group.C?.code} value={group.C?.bid} />
                  <td className="options-strike-cell">{formatNumber(strike)}</td>
                  <ChainCell formatter={formatPrice} title={group.P?.code} value={group.P?.bid} />
                  <ChainCell formatter={formatPrice} title={group.P?.code} value={group.P?.ask} />
                  <ChainCell formatter={formatDelta} value={group.P?.delta} />
                  <ChainCell formatter={formatIv} value={group.P?.iv} />
                  <ChainCell value={group.P?.oi} />
                  <td><button className="options-add-button" disabled={!group.P?.code} onClick={() => onAdd(group.P.code)} type="button" aria-label={`加入 Put ${strike}`}>+</button></td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {!visibleStrikes.length ? <EmptyState title="没有匹配的执行价" detail="筛选条件下没有行；缺失值仍保持为—。" /> : null}
      </section>
      <p className="options-note">所有月份均可通过上方月份卡片切换；零报价月份保留选择入口，不填充示例数字。</p>
    </div>
  );
}

function savedScenarioValue(key, fallback) {
  try { const value = JSON.parse(window.localStorage.getItem('options.scenario.v1') || '{}')[key]; return Number.isFinite(Number(value)) && value !== '' && value != null ? String(value) : fallback; } catch { return fallback; }
}

function shanghaiDate(now = Date.now()) {
  return new Intl.DateTimeFormat('sv-SE', {timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit'}).format(now);
}

function scenarioExitTime(asof, days) {
  if (!asof || toNumber(days) === null) return MISSING;
  return new Intl.DateTimeFormat('sv-SE', {timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'}).format(Date.parse(asof) + Number(days) * 86400000);
}

function SnapshotMeta({ snapshot, now }) {
  const quoted = snapshot?.coverage?.quoted;
  const contracts = snapshot?.coverage?.contracts;
  const historical = snapshot?.trade_date !== shanghaiDate(now) || snapshot?.mode !== 'live';
  return (
    <div className="options-meta-line">
      <span className={`options-quality-tag ${modeClass(snapshot?.mode)}`}>{historical ? '历史截面' : modeLabel(snapshot?.mode)}</span>
      <span>{snapshot?.mode === 'live' ? `${formatDateTime(snapshot?.asof)}（不自动刷新）` : `asof ${formatDateTime(snapshot?.asof)}`}</span>
      <span>交易日 {formatDate(snapshot?.trade_date)}</span><strong>分析基准时点 · 非持续实时行情</strong>
      <span>来源 {snapshot?.source || MISSING}</span>
      <span>覆盖 {formatNumber(quoted)}/{formatNumber(contracts)}</span>
      {snapshotAgeLabel(snapshot?.asof, snapshot?.mode, now) ? <span className="options-age-warning">{snapshotAgeLabel(snapshot?.asof, snapshot?.mode, now)}</span> : null}
    </div>
  );
}

export default function Options() {
  const [product, setProduct] = useState('IM');
  const [productQuery, setProductQuery] = useState(PRODUCT_OPTIONS[0].label);
  const [snapshot, setSnapshot] = useState(null);
  const [snapshotStatus, setSnapshotStatus] = useState('loading');
  const [snapshotError, setSnapshotError] = useState('');
  const [selectedMonthId, setSelectedMonthId] = useState(null);
  const [activeTab, setActiveTab] = useState('workspace');
  const [holdingMetric, setHoldingMetric] = useState('oi');
  const [basket, setBasket] = useState(readBasket);
  const [target, setTarget] = useState(() => savedScenarioValue('target', ''));
  const [days, setDays] = useState(() => savedScenarioValue('days', '1'));
  const [ivShift, setIvShift] = useState(() => savedScenarioValue('ivShift', '0'));
  const [scenario, setScenario] = useState({ status: 'idle', result: null, error: '' });
  const [refreshState, setRefreshState] = useState({ status: 'idle', message: '', error: '' });
  const [now, setNow] = useState(() => Date.now());
  const refreshControllerRef = useRef(null);
  const scenarioRequestRef = useRef(0);

  const fetchSnapshotData = useCallback(async (signal) => {
    const response = await fetch(SNAPSHOT_ENDPOINT, { headers: { Accept: 'application/json' }, signal });
    if (!response.ok) throw new Error(await responseError(response, '快照请求失败'));
    return normalizeSnapshot(await response.json());
  }, []);

  const applySnapshot = useCallback((next) => {
    scenarioRequestRef.current += 1;
    setSnapshot(next);
    setTarget(value => value || String(Math.ceil(next.spot * 1.01 / 50) * 50));
    setSnapshotStatus('ready');
    setSnapshotError('');
    setSelectedMonthId((current) => next.months.some((month) => String(month.id) === String(current)) ? current : (next.months[0]?.id ?? null));
    setScenario({ status: 'idle', result: null, error: '' });
  }, []);

  const loadSnapshot = useCallback(async (signal) => {
    setSnapshotStatus('loading');
    try {
      const next = await fetchSnapshotData(signal);
      applySnapshot(next);
      return next;
    } catch (error) {
      if (error?.name === 'AbortError') return null;
      setSnapshotStatus('error');
      setSnapshotError(error?.message || '无法读取快照');
      return null;
    }
  }, [applySnapshot, fetchSnapshotData]);

  useEffect(() => {
    const controller = new AbortController();
    loadSnapshot(controller.signal);
    return () => controller.abort();
  }, [loadSnapshot]);

  useEffect(() => {
    try {
      window.localStorage.setItem(BASKET_STORAGE_KEY, JSON.stringify(basket));
    } catch {
      // Storage can be unavailable in a restricted browser context; the in-memory basket remains usable.
    }
  }, [basket]);

  useEffect(() => {
    try { window.localStorage.setItem('options.scenario.v1', JSON.stringify({target, days, ivShift})); } catch {}
  }, [target, days, ivShift]);

  useEffect(() => () => refreshControllerRef.current?.abort(), []);

  useEffect(() => {
    if (snapshot?.mode !== 'live') return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), 30000);
    return () => window.clearInterval(timer);
  }, [snapshot?.mode]);

  const months = snapshot?.months || [];
  const selectedMonth = months.find((month) => String(month.id) === String(selectedMonthId)) || months[0] || null;
  const monthRows = useMemo(() => getMonthRows(snapshot?.rows, selectedMonth?.id), [selectedMonth?.id, snapshot?.rows]);
  const rowsByCode = useMemo(() => getRowsByCode(snapshot?.rows), [snapshot?.rows]);
  const selectedProduct = PRODUCT_OPTIONS.find((item) => item.value === product) || PRODUCT_OPTIONS[0];

  const handleProductChange = (value) => {
    setProductQuery(value);
    const matched = PRODUCT_OPTIONS.find((item) => item.label === value || item.value === value || value.toUpperCase().startsWith(`${item.value} `));
    if (matched) {
      setProduct(matched.value);
      if (matched.value !== 'IM') setRefreshState({ status: 'idle', message: '', error: '' });
    }
  };

  const addToBasket = useCallback((code) => {
    if (!code) return;
    scenarioRequestRef.current += 1;
    setScenario({ status: 'idle', result: null, error: '' });
    setBasket((current) => current.some((leg) => leg.code === code) ? current : [...current, { code, side: 'buy', quantity: 1 }]);
  }, []);

  const removeFromBasket = useCallback((index) => {
    scenarioRequestRef.current += 1;
    setScenario({ status: 'idle', result: null, error: '' });
    setBasket((current) => current.filter((_, itemIndex) => itemIndex !== index));
  }, []);

  const updateBasketLeg = useCallback((index, patch) => {
    scenarioRequestRef.current += 1;
    setScenario({ status: 'idle', result: null, error: '' });
    setBasket((current) => current.map((leg, itemIndex) => {
      if (itemIndex !== index) return leg;
      const nextQuantity = patch.quantity === undefined ? leg.quantity : toNumber(patch.quantity);
      return {
        ...leg,
        ...patch,
        side: patch.side === 'sell' ? 'sell' : patch.side === 'buy' ? 'buy' : leg.side,
        quantity: Math.max(1, Math.min(9999, Math.trunc(nextQuantity === null ? leg.quantity : nextQuantity))),
      };
    }));
  }, []);

  const updateScenarioInput = useCallback((setter, value) => {
    scenarioRequestRef.current += 1;
    setScenario({ status: 'idle', result: null, error: '' });
    setter(value);
  }, []);

  const calculateScenario = async () => {
    const requestId = scenarioRequestRef.current + 1;
    scenarioRequestRef.current = requestId;
    if (!snapshot?.snapshot_id) {
      setScenario({ status: 'error', result: null, error: '当前行情截面缺少唯一标识，暂不能计算' });
      return;
    }
    const targetNumber = toNumber(target);
    const daysNumber = toNumber(days);
    const ivShiftNumber = toNumber(ivShift);
    if (targetNumber === null || daysNumber === null || ivShiftNumber === null || daysNumber < 0 || basket.length === 0) {
      setScenario({ status: 'error', result: null, error: '请填写有效的目标点位、持有天数和 IV 平移，并至少加入一个合约' });
      return;
    }
    setScenario({ status: 'loading', result: null, error: '' });
    try {
      const response = await fetch(SCENARIO_ENDPOINT, {
        method: 'POST',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
        body: JSON.stringify({
          snapshot_id: snapshot.snapshot_id,
          legs: basket.map((leg) => ({ code: leg.code, side: leg.side, quantity: leg.quantity })),
          target: targetNumber,
          days: daysNumber,
          iv_shift_pp: ivShiftNumber,
        }),
      });
      if (!response.ok) throw new Error(await responseError(response, '情景请求失败'));
      const result = await response.json();
      if (requestId !== scenarioRequestRef.current) return;
      setScenario({ status: 'ready', result, error: '' });
    } catch (error) {
      if (requestId !== scenarioRequestRef.current || error?.name === 'AbortError') return;
      setScenario({ status: 'error', result: null, error: error?.message || '无法计算情景' });
    }
  };

  const refreshLive = async () => {
    if (product !== 'IM' || refreshState.status === 'loading') return;
    refreshControllerRef.current?.abort();
    const controller = new AbortController();
    refreshControllerRef.current = controller;
      setRefreshState({ status: 'loading', message: '正在提交盘中截面查询…', error: '' });
    const beforeId = snapshot?.snapshot_id;
    try {
      const response = await fetch(REFRESH_ENDPOINT, {
        method: 'POST',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
        body: JSON.stringify({ product }),
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(await responseError(response, '盘中截面查询失败'));
      const refreshResult = await response.json();
      const status = String(refreshResult?.status || '').toLowerCase();
      if (['failed', 'error', 'rejected'].includes(status)) throw new Error(refreshResult.message || '盘中截面查询未完成');
      const targetId = refreshResult?.snapshot_id;
      const completed = ['complete', 'completed', 'success', 'ok', 'ready'].includes(status);
      let latest = null;
      let matched = false;
      for (let attempt = 0; attempt < 20; attempt += 1) {
        if (attempt > 0) await new Promise((resolve) => window.setTimeout(resolve, 700));
        latest = await fetchSnapshotData(controller.signal);
        matched = targetId ? latest.snapshot_id === targetId : latest.snapshot_id !== beforeId || latest.mode === 'live';
        if (matched || completed) break;
        setRefreshState({ status: 'loading', message: `盘中截面查询进行中… ${attempt + 1}/20`, error: '' });
      }
      if (latest && (matched || completed)) {
        applySnapshot(latest);
        setRefreshState({ status: 'done', message: '已载入新的手动盘中截面', error: '' });
      } else {
        setRefreshState({ status: 'done', message: '查询已提交，新盘中截面尚未返回', error: '' });
      }
    } catch (error) {
      if (error?.name === 'AbortError') return;
      setRefreshState({ status: 'error', message: '', error: error?.message || '盘中截面查询失败' });
    }
  };

  const content = product !== 'IM' ? (
    <EmptyState title={`${selectedProduct.label} 尚未接入`} detail={`${selectedProduct.mapping} 暂无接口数据；当前页面不会填充示例数字。`} />
  ) : snapshotStatus === 'loading' && !snapshot ? (
    <div className="options-loading"><span className="options-spinner" />正在读取期权快照…</div>
  ) : snapshotStatus === 'error' && !snapshot ? (
    <div className="options-error-state"><h2>快照读取失败</h2><p>{snapshotError}</p><button className="options-primary-button" onClick={() => loadSnapshot()} type="button">重试</button></div>
  ) : !snapshot ? (
    <EmptyState title="暂无期权快照" detail="等待后端返回可用数据。" />
  ) : !months.length ? (
    <EmptyState title="快照没有月份" detail="接口未返回到期月份，无法展开期权页面。" />
  ) : (
    <>
      <div className="options-quote-row">
        <span>中证1000 <strong>{formatPrice(snapshot.spot)}</strong></span>
        <span className="options-muted">期权映射 {selectedProduct.mapping}</span>
        <span className="options-quote-note">覆盖 {formatNumber(snapshot.coverage?.quoted)}/{formatNumber(snapshot.coverage?.contracts)} · 无数据字段显示为{MISSING}</span>
      </div>
      <p className="options-time-context">{activeTab === 'exit' ? '退出分析支持单独回看历史截面；请以页内基准时点、组合和费用设置为准。' : `${snapshot.trade_date !== shanghaiDate(now) ? `正在回看 ${snapshot.trade_date} 历史截面；` : ''}下方报价、剩余期限和情景计算统一以 ${formatDateTime(snapshot.asof)} 为基准。`}</p>
      <MonthStrip months={months} onSelect={setSelectedMonthId} selectedMonthId={selectedMonth?.id} />
      <div className="options-tab-nav" role="tablist" aria-label="期权分析页签">
        {TAB_ITEMS.map((tab) => <button aria-selected={activeTab === tab.id} className={activeTab === tab.id ? 'options-tab-active' : ''} key={tab.id} onClick={() => setActiveTab(tab.id)} role="tab" type="button">{tab.label}</button>)}
      </div>
      {snapshot.warnings.length ? <details className="options-methods"><summary>数据与计算口径（{snapshot.warnings.length} 项）</summary><div className="options-warning-strip">{snapshot.warnings.map((warning, index) => <span key={`${warning}-${index}`}>{warning}</span>)}</div></details> : null}
      {activeTab === 'workspace' ? <WorkspaceTab snapshot={snapshot} month={selectedMonth} onLoad={(legs, result) => { scenarioRequestRef.current += 1; setBasket(legs); setScenario({ status: result ? 'ready' : 'idle', result, error: '' }); }} basket={basket} days={days} ivShift={ivShift} months={months} onCalculate={calculateScenario} onDays={(value) => updateScenarioInput(setDays, value)} onIvShift={(value) => updateScenarioInput(setIvShift, value)} onLegChange={updateBasketLeg} onLegRemove={removeFromBasket} onTarget={(value) => updateScenarioInput(setTarget, value)} rowsByCode={rowsByCode} scenario={scenario} target={target} /> : null}
      {activeTab === 'exit' ? <ExitLab snapshot={snapshot} month={selectedMonth} basket={basket} target={target} /> : null}
      {activeTab === 'volatility' ? <VolatilityTab months={months} rows={snapshot.rows} month={selectedMonth} spot={snapshot.spot} /> : null}
      {activeTab === 'holding' ? <HoldingTab snapshot={snapshot} metric={holdingMetric} months={months} onMetric={setHoldingMetric} onOpenChain={() => setActiveTab('chain')} rows={snapshot.rows} month={selectedMonth} spot={snapshot.spot} /> : null}
      {activeTab === 'chain' ? <ChainTab months={months} onAdd={addToBasket} onSelectMonth={setSelectedMonthId} rows={snapshot.rows} month={selectedMonth} spot={snapshot.spot} /> : null}
      {activeTab !== 'exit' ? <BasketBar basket={basket} months={months} onRemove={removeFromBasket} rowsByCode={rowsByCode} /> : null}
    </>
  );

  return (
    <div className="options-module">
      <header className="options-topbar">
        <div className="options-brand-block">
          <span className="options-brand">期权终端<span className="options-brand-dot">.</span></span>
          <label className="options-product-label">
            <span className="options-sr-only">搜索品种</span>
            <input list="options-products" onBlur={() => setProductQuery(selectedProduct.label)} onChange={(event) => handleProductChange(event.target.value)} value={productQuery} aria-label="搜索品种" />
            <datalist id="options-products">{PRODUCT_OPTIONS.map((item) => <option key={item.value} value={item.label} />)}</datalist>
          </label>
        </div>
        <div className="options-top-actions">
          {snapshot ? <SnapshotMeta now={now} snapshot={snapshot} /> : <span className="options-meta-line">等待数据</span>}
          <button className="options-refresh-button" disabled={product !== 'IM' || refreshState.status === 'loading'} onClick={refreshLive} type="button">{refreshState.status === 'loading' ? '查询中…' : '手动刷新盘中截面'}</button>
        </div>
      </header>
      {refreshState.message ? <div className="options-refresh-status">{refreshState.message}</div> : null}
      {refreshState.error ? <div className="options-refresh-error">{refreshState.error}</div> : null}
      {snapshotStatus === 'error' && snapshot ? <div className="options-refresh-error">当前快照读取异常：{snapshotError}</div> : null}
      <main className="options-main">{content}</main>
      <footer className="options-footer"><span>数据页 · 无实际执行</span><span>红涨绿跌 · 执行价由高到低</span></footer>
    </div>
  );
}
