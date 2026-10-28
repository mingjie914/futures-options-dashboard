import { conditionalFetch as fetch } from './transport.js';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useChart, Metric, usePreference } from './charts.js';
import './options.css';
import SurfaceComparison, { IvHistory } from './SurfaceComparison.jsx';
import { holdingRows, putCallRatio } from './holdings.js';

const SNAPSHOT_ENDPOINT = '/api/options/v1/snapshot';
const MISSING = '—';
const MONTH_COLORS = ['#4e7fb9', '#8c7b9f', '#568f84', '#a48b59', '#505c6b', '#a17589'];
const TAB_ITEMS = [
  { id: 'volatility', label: '波动率' },
  { id: 'holding', label: '持仓成交' },
  { id: 'chain', label: '期权链' },
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
  return '已保存截面 · 非持续实时行情';
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
  const match = text.match(/^([A-Za-z]+)(\d{4})(.*)$/);
  if (!match) return <span className="options-code">{text}</span>;
  return <span className="options-code">{match[1]}{match[2].slice(0,2)}<span style={{color}}>{match[2].slice(2)}</span>{match[3]}</span>;
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
            title={`${formatDate(month.expiry)} 到期 · ${formatNumber(quoted)}/${formatNumber(contracts)} 报价`}
            style={{ borderTopColor: color }}
            type="button"
          >
            <strong><ColoredCode code={month.code || `MO${String(month.id).slice(-4)}`} color={color} /></strong>
            <em>截面剩余 {formatNumber(month.calendar_days)} 自然日</em>

          </button>
        );
      })}
    </div>
  );
}

function VolatilityTab({ rows, month, months, spot, snapshot }) {
  const [view, setView] = usePreference('options.volatility.view','single');
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

  useChart(chartRef, (chart) => {
    const spotValue = toNumber(spot);
    chart.setOption({
      animation: false,
      color: ['#4e7fb9', '#a17589'],
      grid: { left: 48, right: 22, top: 44, bottom: 42 },
      legend: { top: 6, right: 8, itemWidth: 14, itemHeight: 8, textStyle: { color: '#505c6b', fontSize: 12 } },
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
        axisLabel: { color: '#505c6b', fontSize: 11 },
        axisLine: { lineStyle: { color: '#eef0f3' } },
        splitLine: { lineStyle: { color: '#eef0f3' } },
      },
      yAxis: {
        type: 'value',
        scale: true,
        name: 'IV (%)',
        nameTextStyle: { color: '#505c6b', fontSize: 11 },
        axisLabel: { color: '#505c6b', fontSize: 11, formatter: (value) => `${value}%` },
        axisLine: { show: false },
        splitLine: { lineStyle: { color: '#eef0f3' } },
      },
      series: [
        { name: 'Call IV', type: 'line', data: callPoints, showSymbol: true, symbolSize: 7, connectNulls: false, lineStyle: { width: 2 } },
        { name: 'Put IV', type: 'line', data: putPoints, showSymbol: true, symbolSize: 7, connectNulls: false, lineStyle: { width: 2 } },
      ],
    });
    if (spotValue !== null) {
      chart.setOption({
        series: [
          { markLine: { silent: true, symbol: 'none', lineStyle: { color: '#505c6b', type: 'dashed' }, label: { formatter: '现货', color: '#505c6b' }, data: [{ xAxis: spotValue }] } },
          { markLine: { silent: true, data: [] } },
        ],
      });
    }
  }, [callPoints, putPoints, spot, view]);

  const hasPoints = callPoints.length || putPoints.length;
  const nearestSet = useMemo(() => nearestStrikes(uniqueStrikes(monthRows), spot, 3), [monthRows, spot]);
  return (
    <div className="options-tab-panel">
      <IvHistory monthId={month?.id} rows={monthRows} spot={spot} snapshot={snapshot}/>
      <div className="options-heading">
        <div>
          <h2>波动率微笑</h2>
          <p className="options-muted"><ColoredCode code={month?.code || month?.id} color={monthColor(months, month?.id)} /></p>
        </div>
        <span className="options-section-tag">{formatNumber(callPoints.length + putPoints.length)} 个 IV 点</span>
      </div>
      <div className="options-segment options-analysis-tools">{[['single','单月微笑'],['smiles','跨月微笑'],['term','ATM 期限结构'],['skew','25Δ 偏度']].map(([id,label])=><button key={id} type="button" className={view===id?'options-segment-on':''} onClick={()=>setView(id)}>{label}</button>)}</div>
      {view !== 'single' ? <SurfaceComparison rows={rows} months={months} metric={view} /> : <section className="options-surface options-chart-surface">
        {hasPoints ? <div className="options-vol-chart" ref={chartRef} aria-label="实际 IV 微笑图" /> : <EmptyState title="当前月份没有可绘制 IV" />}
      </section>}
      <details className="options-surface"><summary>现货附近报价与 IV 明细</summary>
        <div className="options-heading options-heading-tight">
          <div>
            <h3>现货附近合约</h3>

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
      </details>
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
  const [basis, setBasis] = usePreference('options.holding.basis','daily');
  const [previous, setPrevious] = useState(null);
  const [previousMessage, setPreviousMessage] = useState('正在读取上一截面…');
  useEffect(() => {
    const controller = new AbortController();
    setPrevious(null); setPreviousMessage('正在读取上一截面…');
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
  const basisLabel = basis === 'snapshot' ? (comparable ? `较上一截面 ${formatDateTime(previous.asof)}` : '同日上一截面') : '较前一交易日持仓';
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
  const pcr = useMemo(() => ['oi', 'volume'].map(field => putCallRatio(getMonthRows(rows, month?.id), field, toNumber(month?.contracts))), [rows, month?.id, month?.contracts]);

  return (
    <div className="options-tab-panel">
      <div className="options-heading">
        <div>
          <h2>持仓分布与增减</h2>
          <p className="options-muted"><ColoredCode code={month?.code || month?.id} color={monthColor(months, month?.id)} /></p>
        </div>
        <div className="options-heading-actions">
          <div className="options-segment">
            {['oi', 'volume'].map((value) => <button className={metric === value ? 'options-segment-on' : ''} key={value} onClick={() => onMetric(value)} type="button">{value === 'oi' ? '持仓量 + 增减' : '成交量'}</button>)}
          </div>
          <button className="options-plain-button" onClick={onOpenChain} type="button">打开期权链</button>
        </div>
      </div>
      <div className="options-pcr" aria-label="所选月份 PCR">{pcr.map((value,index)=><Metric key={index} label={(index?'成交':'持仓')+' PCR'+(snapshot.test_only?' · 样本':'')} value={formatNumber(value.ratio,2)} hint={'所选月份 Put合计 ÷ Call合计；覆盖 '+value.covered+'/'+value.total+' 合约'+(value.reason?'；'+value.reason:'')}/>)}</div>
      <div className="options-analysis-tools"><label>变化基准<select value={basis} onChange={e => setBasis(e.target.value)}><option value="daily">前一交易日</option><option value="snapshot" disabled={!comparable}>同日上一截面</option></select></label><span className="options-muted">{basis === 'snapshot' ? (previousMessage || basisLabel) : ''}</span></div>

      <div className="options-holding-layout">
        <section className="options-surface">
          <div className="options-heading options-heading-tight">
            <div>
              <h3>执行价分布 <span className="options-muted options-small">单位：手</span></h3>
              <p className="options-holding-explain">{metric === 'oi' ? `实心为持仓，红段为增仓，外侧虚线为减仓。${basisLabel}。` : (basis === 'snapshot' ? '成交量为两次截面之间的增量；负修订或缺失值显示为—。' : '当日累计成交量（手）')}</p>
            </div>
            {metric === 'oi' && <div className="options-legend"><span><i className="options-dot options-dot-blue" />存量</span><span><i className="options-dot options-dot-red" />增仓</span><span><i className="options-dot options-dot-green" />减仓</span></div>}
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
          <div className="options-spot-line">── 现货 {formatPrice(spot)} ──</div>
        </section>

      </div>
      <details className="options-surface"><summary>成交与增减仓排行</summary>
      <div className="options-ranking-grid">{rankings.map(([title, items, field]) => <section className="options-surface" key={title}><h3>{title}</h3>{items.length ? items.map(row => <div className="options-rank-row" key={row.code}><ColoredCode code={row.code} color={monthColor(months,row.month)}/><strong className={field === 'oi_change' ? (row[field] > 0 ? 'options-up' : 'options-down') : ''}>{field === 'oi_change' ? formatSigned(row[field]) : formatNumber(row[field])} 手</strong></div>) : <p className="options-note">没有可比较的有效变化</p>}</section>)}</div>
      </details>

    </div>
  );
}

function ChainCell({ value, formatter, className = '', title }) {
  return <td className={className} title={title}>{formatter ? formatter(value) : formatNumber(value)}</td>;
}

function ChainTab({ rows, month, months, spot }) {
  const [search, setSearch] = usePreference('options.chain.search','');
  const [filter, setFilter] = usePreference('options.chain.filter','all');
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
          <h2>期权链</h2>
          <p className="options-muted"><ColoredCode code={code} color={color} /></p>
        </div>

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
          <colgroup><col span={11} style={{ width: `${100 / 11}%` }} /></colgroup>
          <thead>
            <tr className="options-chain-group-head">
              <th colSpan="5"><span className="options-call-label">CALL · 看涨期权</span> <ColoredCode code={code} color={color} /></th>
              <th className="options-strike-head">执行价</th>
              <th colSpan="5"><span className="options-put-label">PUT · 看跌期权</span> <ColoredCode code={code} color={color} /></th>
            </tr>
            <tr>
              <th>OI</th><th>IV</th><th>Delta</th><th>卖价</th><th>买价</th>
              <th className="options-strike-head">高 → 低</th>
              <th>买价</th><th>卖价</th><th>Delta</th><th>IV</th><th>OI</th>
            </tr>
          </thead>
          <tbody>
            {visibleStrikes.map((strike) => {
              const group = groups.get(strike) || {};
              return (
                <tr className={nearSet.has(strike) ? 'options-near-row' : ''} key={strike}>
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
                </tr>
              );
            })}
          </tbody>
        </table>
        {!visibleStrikes.length ? <EmptyState title="没有匹配的执行价" /> : null}
      </section>

    </div>
  );
}

function shanghaiDate(now = Date.now()) {
  return new Intl.DateTimeFormat('sv-SE', {timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit'}).format(now);
}

function SnapshotMeta({ snapshot }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (snapshot?.mode !== 'live') return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), 30000);
    return () => window.clearInterval(timer);
  }, [snapshot?.mode]);
  const historical = snapshot?.trade_date !== shanghaiDate(now) || snapshot?.mode !== 'live';
  return (
    <div className="options-meta-line">
      <span className={`options-quality-tag ${modeClass(snapshot?.mode)}`}>{historical ? '历史截面' : modeLabel(snapshot?.mode)}</span>
      <span>{formatDateTime(snapshot?.asof)}</span>
      {snapshot?.trade_date!==String(snapshot?.asof).slice(0,10)&&<span>交易日 {formatDate(snapshot?.trade_date)}</span>}
      <span>{snapshot?.test_only?'测试样本 · 部分合约':snapshot?.source || MISSING}</span>
      {snapshotAgeLabel(snapshot?.asof, snapshot?.mode, now) ? <span className="options-age-warning">{snapshotAgeLabel(snapshot?.asof, snapshot?.mode, now)}</span> : null}
    </div>
  );
}

export default function Options() {
  const [snapshot, setSnapshot] = useState(null);
  const [snapshotStatus, setSnapshotStatus] = useState('loading');
  const [snapshotError, setSnapshotError] = useState('');
  const [selectedMonthId, setSelectedMonthId] = useState(() => new URLSearchParams(location.search).get('month'));
  const [activeTab, setActiveTab] = useState(() => {
    const tab = new URLSearchParams(location.search).get('tab');
    return TAB_ITEMS.some(item => item.id === tab) ? tab : 'volatility';
  });
  const [holdingMetric, setHoldingMetric] = usePreference('options.holding.metric','oi');

  const fetchSnapshotData = useCallback(async (signal) => {
    const response = await fetch(SNAPSHOT_ENDPOINT, { headers: { Accept: 'application/json' }, signal });
    if (!response.ok) throw Object.assign(new Error(await responseError(response, '快照请求失败')), { empty: response.headers.get('X-Snapshot-State') === 'empty' });
    return normalizeSnapshot(await response.json());
  }, []);

  const applySnapshot = useCallback((next) => {
    setSnapshot(next);
    setSnapshotStatus('ready');
    setSnapshotError('');
    setSelectedMonthId((current) => next.months.some((month) => String(month.id) === String(current)) ? current : (next.months[0]?.id ?? null));
  }, []);

  const loadSnapshot = useCallback(async (signal) => {
    setSnapshotStatus('loading');
    try {
      const next = await fetchSnapshotData(signal);
      applySnapshot(next);
      return next;
    } catch (error) {
      if (error?.name === 'AbortError') return null;
      setSnapshotStatus(error.empty ? 'empty' : 'error');
      setSnapshotError(error?.message || '无法读取快照');
      return null;
    }
  }, [applySnapshot, fetchSnapshotData]);

  useEffect(() => {
    const url = new URL(location.href);
    url.searchParams.set('tab', activeTab);
    if (selectedMonthId) url.searchParams.set('month', selectedMonthId);
    else url.searchParams.delete('month');
    history.replaceState(null, '', url);
  }, [activeTab, selectedMonthId]);

  useEffect(() => {
    const controller = new AbortController();
    loadSnapshot(controller.signal);
    return () => controller.abort();
  }, [loadSnapshot]);

  const months = snapshot?.months || [];
  const selectedMonth = months.find((month) => String(month.id) === String(selectedMonthId)) || months[0] || null;
  const content = snapshotStatus === 'loading' && !snapshot ? (
    <div className="options-loading"><span className="options-spinner" />正在读取期权快照…</div>
  ) : snapshotStatus === 'empty' && !snapshot ? (
    <EmptyState title="期权数据尚未接入" />
  ) : snapshotStatus === 'error' && !snapshot ? (
    <div className="options-error-state"><h2>快照读取失败</h2><p>{snapshotError}</p><button className="options-primary-button" onClick={() => loadSnapshot()} type="button">重试</button></div>
  ) : !snapshot ? (
    <EmptyState title="暂无期权快照" />
  ) : !months.length ? (
    <EmptyState title="快照没有月份" />
  ) : (
    <>
      <div className="options-market-tools"><div className="options-quote-row">
        <span>中证1000 <strong>{formatPrice(snapshot.spot)}</strong></span>

      </div>

      <MonthStrip months={months} onSelect={setSelectedMonthId} selectedMonthId={selectedMonth?.id} />
      <p className="options-note">{formatDate(selectedMonth?.expiry)} 到期 · 当前月份报价 {formatNumber(selectedMonth?.quoted)}/{formatNumber(selectedMonth?.contracts)}</p>

      </div>
      {activeTab === 'volatility' ? <VolatilityTab months={months} rows={snapshot.rows} month={selectedMonth} spot={snapshot.spot} snapshot={snapshot}/> : null}
      {activeTab === 'holding' ? <HoldingTab snapshot={snapshot} metric={holdingMetric} months={months} onMetric={setHoldingMetric} onOpenChain={() => setActiveTab('chain')} rows={snapshot.rows} month={selectedMonth} spot={snapshot.spot} /> : null}
      {activeTab === 'chain' ? <ChainTab months={months} rows={snapshot.rows} month={selectedMonth} spot={snapshot.spot} /> : null}
    </>
  );

  return (
    <div className="options-module board-content">
      <div className="board-tabs" role="tablist" aria-label="期权分析页签">
        {TAB_ITEMS.map((tab) => <button aria-selected={activeTab === tab.id} key={tab.id} onClick={() => setActiveTab(tab.id)} role="tab" type="button">{tab.label}</button>)}
      </div>
      <header className="options-topbar">
        <div className="options-brand-block">
          <h1 className="options-brand">期权研究</h1>

        </div>
        {snapshot ? <SnapshotMeta snapshot={snapshot} /> : <span className="options-meta-line">等待数据</span>}
        <div className="options-top-actions">
          <button className="options-refresh-button" disabled={snapshotStatus === 'loading'} onClick={() => loadSnapshot()} type="button">{snapshotStatus === 'loading' ? '读取中…' : '刷新数据'}</button>
        </div>
      </header>
      {snapshotStatus === 'error' && snapshot ? <div className="options-refresh-error">当前快照读取异常：{snapshotError}</div> : null}
      <main className="options-main">
{content}</main>

    </div>
  );
}
