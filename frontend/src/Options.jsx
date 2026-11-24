import { conditionalFetch as fetch } from './transport.js';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useChart, Metric, usePreference, formatPrice as price } from './charts.js';
import './options.css';
import SurfaceComparison, { ColoredCode, MONTH_COLORS, TermOverview } from './SurfaceComparison.jsx';
import { surfaceMetrics } from './surfaceMetrics.js';
import { holdingRows, putCallRatio, optionSmile, optionStrikeTooltip, OPTION_IV_CURVE, OPTION_SIDES, OPTION_STRIKE_TOOLTIP } from './holdings.js';

const SNAPSHOT_ENDPOINT = '/api/options/v1/snapshot';
const MISSING = '—';
const TAB_ITEMS = [
  { id: 'analysis', label: '期权分析' },
  { id: 'chain', label: '期权链' },
];
function toNumber(value) {
  if (value === null || value === undefined || value === '') return null;
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

const numberFormats=new Map();
function formatNumber(value,digits=0,useGrouping=true) {
  const number=toNumber(value);
  if(number===null)return MISSING;
  const key=digits+':'+useGrouping;
  if(!numberFormats.has(key))numberFormats.set(key,new Intl.NumberFormat('en-US',{minimumFractionDigits:digits,maximumFractionDigits:digits,useGrouping}));
  return numberFormats.get(key).format(number);
}

function formatPrice(value, digits = 1) {
  return price(value, digits, digits);
}

function formatIv(value) {
  const number = toNumber(value);
  return number === null ? MISSING : `${formatNumber(number * 100, 1, false)}%`;
}

function formatDelta(value) {
  return formatNumber(value, 2, false);
}

function formatDate(value) {
  if (value === null || value === undefined || value === '') return MISSING;
  return String(value).slice(0, 10);
}

function formatDte(month) {
  return `${formatNumber(month?.trading_days, 0, false)} DTE`;
}

function monthColor(months, monthId) {
  const index = months.findIndex((month) => String(month.id) === String(monthId));
  return MONTH_COLORS[(index < 0 ? 0 : index) % MONTH_COLORS.length];
}

function normalizeSnapshot(value) {
  const raw = value && typeof value === 'object' ? value : {};
  const months = Array.isArray(raw.months)
    ? raw.months.filter(Boolean).map((month) => ({ ...month, id: String(month.id ?? '') }))
    : [];
  const rows = Array.isArray(raw.rows)
    ? raw.rows.filter(Boolean)
    : [];
  return {
    ...raw,
    mode: ['live', 'eod'].includes(raw.mode) ? raw.mode : 'replay',
    months,
    rows,
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

function EmptyState({ title }) {
  return <div className="options-empty"><h2>{title}</h2></div>;
}

function MonthStrip({ months, selectedMonthId, onSelect }) {
  return (
    <div className="options-months" aria-label="到期月份">
      {months.map((month, index) => {
        const color = MONTH_COLORS[index % MONTH_COLORS.length];
        return (
          <button
            className={`options-month ${String(month.id) === String(selectedMonthId) ? 'options-month-selected' : ''}`}
            key={month.id || index}
            onClick={() => onSelect(month.id)}
            aria-pressed={String(month.id) === String(selectedMonthId)}
            type="button"
          >
            <strong><ColoredCode code={month.code || `MO${String(month.id).slice(-4)}`} color={color} /></strong>
            <em>{formatDte(month)}</em>
          </button>
        );
      })}
    </div>
  );
}

function rowsByMonth(rows) {
  const index = new Map();
  for (const row of rows) {
    const month = String(row.month);
    if (!index.has(month)) index.set(month, []);
    index.get(month).push(row);
  }
  return index;
}

function smilePoints(groups, spot) {
  const chain = groups.map(([strike, legs]) => ({ strike, ...legs }));
  return optionSmile(chain, toNumber(spot)).map(([strike, iv]) => [strike, iv === null ? null : iv * 100]);
}

const NO_ROWS = [];
const SURFACE_VIEWS = [['months','到期月分布'],['smiles','跨月微笑'],['term','ATM 期限结构'],['skew','25Δ 偏度']];

function MonthChart({ snapshot, rows, previousRows, month, color, basis, comparable, onOpenChain }) {
  const chartRef = useRef(null);
  const monthRows = useMemo(() => holdingRows(rows, comparable, basis), [rows, comparable, basis]);
  const previousGroups = useMemo(() => [...groupRowsByStrike(previousRows)].sort(([a], [b]) => a - b), [previousRows]);
  const groups = useMemo(() => {
    const byStrike = groupRowsByStrike(monthRows);
    for (const [strike] of previousGroups) {
      if (!byStrike.has(strike)) byStrike.set(strike, {});
    }
    return [...byStrike].sort(([a], [b]) => a - b);
  }, [monthRows, previousGroups]);
  const { spot, trade_date: date, previous_eod: prior } = snapshot;
  const eod = snapshot.mode === 'eod';
  const reference = eod ? month.forward : spot;
  const previousReference = eod ? prior?.months?.find(item => String(item.id) === month.id)?.forward : prior?.spot;
  const points = useMemo(() => smilePoints(groups, reference), [groups, reference]);
  const previousPoints = useMemo(() => smilePoints(previousGroups, previousReference), [previousGroups, previousReference]);
  const hasPrevious = previousPoints.some(([, iv]) => iv !== null);
  const hasIv = points.some(([, iv]) => iv !== null) || hasPrevious;
  const showVolume = monthRows.some(row => Number.isFinite(row.volume));
  const hasOi = monthRows.some(row => Number.isFinite(row.oi));
  const hasHolding = hasOi || showVolume;
  const pcr = useMemo(() => ['oi', 'volume'].map(field => putCallRatio(rows, field, toNumber(month.contracts))), [rows, month.contracts]);

  useChart(chartRef, chart => {
    const strikeValues = values => groups.map(([strike], i) => [strike, values[i]]);
    const spotValue = toNumber(spot);
    const atMoney = spotValue === null ? undefined : {silent: true, symbol: 'none', label: {show: false},
      lineStyle: {color: '#aeb5bf', type: 'dashed'}, data: [{xAxis: spotValue}]};
    const ivLine = (name, values, color, dashed = false) => {
      const byStrike = new Map(values);
      const data = groups.map(([strike]) => [strike, byStrike.get(strike) ?? null]);
      return {id: dashed ? 'iv-previous' : 'iv-current', name, type: 'line', z: 5, symbol: 'circle', symbolSize: 4, ...OPTION_IV_CURVE, data,
        itemStyle: {color, opacity: 0.35}, emphasis: {itemStyle: {opacity: 1}}, lineStyle: {color, width: dashed ? 1.3 : 2, type: dashed ? 'dashed' : 'solid'},
        ...(!dashed && {markLine: atMoney})};
    };
    const iv = [
      ivLine(date || 'IV', points, '#3d70ad'),
      ...(hasPrevious ? [ivLine(prior.trade_date, previousPoints, '#7e8996', true)] : []),
    ];
    const available = Number.isFinite;
    const reduced = monthRows.some(row => available(row.oi) && row.oi_change < 0);
    const holding = !hasHolding ? [] : OPTION_SIDES.flatMap(([side, name, color], index) => {
      const base = [], added = [], removed = [], volume = [];
      for (const [, legs] of groups) {
        const {oi, oi_change: delta, volume: traded} = legs[side] || {};
        const comparable = available(oi) && available(delta) && oi - delta >= 0;
        base.push(available(oi) ? oi - (comparable ? Math.max(0, delta) : 0) : null);
        added.push(comparable && delta > 0 ? delta : null);
        removed.push(comparable && delta < 0 ? delta : null);
        volume.push(traded ?? null);
      }
      const bar = {type: 'bar', name, stack: side, yAxisIndex: 1, barMaxWidth: 8};
      const reference = [...(!hasIv ? atMoney?.data ?? [] : []), ...(reduced ? [{yAxis: 0}] : [])];
      return [
        {...bar, id: side + 'added', data: strikeValues(added), itemStyle: {color}},
        {...bar, id: side + 'removed', data: strikeValues(removed),
          itemStyle: {color: 'transparent', borderColor: color, borderType: 'dashed', borderWidth: 1.3}},
        {...bar, id: side + 'base', data: strikeValues(base), itemStyle: {color, opacity: 0.5},
          ...(index === 0 && reference.length && {markLine: {silent: true, symbol: 'none', label: {show: false},
            lineStyle: {color: '#aeb5bf', type: 'dashed'}, data: reference}})},
        ...(showVolume ? [{id: side + 'volume', type: 'line', name: name + '成交',
          yAxisIndex: 1, data: strikeValues(volume), showSymbol: false, connectNulls: false, z: 4,
          itemStyle: {color}, lineStyle: {color, width: 1.1, opacity: 0.7, type: 'dashed'}}] : []),
      ];
    });
    const verticalAxis = {type: 'value', axisLine: {show: false}, axisTick: {show: false}, splitLine: {show: false}};
    chart.setOption({
      animation: false,
      grid: {left: hasIv ? 43 : 12, right: hasHolding ? 50 : 12, top: 32, bottom: 24},
      legend: {type: 'scroll', top: 0, left: 0, right: 0, padding: [5, 0], itemGap: 7, itemWidth: 10, itemHeight: 7,
        textStyle: {color: '#455161', fontSize: 11}, inactiveColor: '#778392',
        selected: showVolume && hasOi ? {'认购成交': false, '认沽成交': false} : {},
        data: [
          ...(hasIv ? iv.map(({name}) => name) : []),
          ...(hasOi ? OPTION_SIDES.map(([, name]) => name) : []),
          ...(showVolume ? OPTION_SIDES.map(([, name]) => name + '成交') : []),
        ],
        formatter: name => name === iv[0].name ? hasPrevious ? '本日 IV' : 'IV' :
          hasPrevious && name === prior.trade_date ? '前日 IV' : name},
      tooltip: {...OPTION_STRIKE_TOOLTIP,
        formatter: items => optionStrikeTooltip(items, groups[items[0]?.dataIndex]?.[1])},
      xAxis: {type: 'value', scale: true, axisPointer: {snap: true}, splitLine: {show: false}, splitNumber: 5,
        axisLabel: {hideOverlap: true, color: '#455161', fontSize: 11, formatter: value => price(value)},
        axisLine: {show: false, onZero: false}, axisTick: {show: false}},
      yAxis: [
        {...verticalAxis, position: 'left', show: hasIv, scale: true, splitNumber: 4,
          axisLabel: {color: '#455161', fontSize: 11, formatter: '{value}%'}},
        {...verticalAxis, position: 'right', show: hasHolding, splitNumber: 4,
          min: ({min, max}) => Math.max(Math.min(min, 0), -0.2 * Math.max(max, 1)),
          axisLabel: {color: '#455161', fontSize: 11, showMinLabel: !reduced, formatter: value => value === 0 ? '0 手' : formatNumber(value)}},
      ],
      series: [...(hasIv ? iv : []), ...holding],
    }, true);
  }, [groups, points, previousPoints, hasPrevious, hasIv, hasHolding, hasOi, showVolume, date, prior, spot]);

  return <section className="options-month-panel" id={`options-month-${month.id}`}>
    <div className="options-month-heading">
      <h2><ColoredCode code={month.code} color={color} /></h2>
      <span className="options-muted">{formatDte(month)} / <time dateTime={month.expiry}>{formatDate(month.expiry)}</time></span>
      <button className="options-plain-button" type="button" onClick={() => onOpenChain(month.id)}>期权链</button>
    </div>
    <div className="options-pcr">{pcr.map((value, index) => <Metric key={index} label={(index ? '成交' : '持仓') + ' PCR' + (snapshot.test_only ? ' · 样本' : '')}
      value={formatNumber(value.ratio, 2, false)} hint={value.reason || undefined}/>)}</div>
    {hasIv || hasHolding ? <div className="options-month-chart" ref={chartRef}
      role="img" aria-label={`${month.code} 隐波、持仓与成交`} /> : <EmptyState title="暂无数据" />}
  </section>;
}

function OptionsAnalysis({ snapshot, onOpenChain }) {
  const {rows, months} = snapshot;
  const [preferredView, setView] = usePreference('options.analysis.view', 'months');
  const eod = snapshot.mode === 'eod';
  const views = eod ? SURFACE_VIEWS.slice(0, 2) : SURFACE_VIEWS;
  const view = views.some(([id]) => id === preferredView) ? preferredView : 'months';
  const needsSurface = eod || view !== 'months';
  const surface = useMemo(() => needsSurface ? surfaceMetrics(rows, months) : NO_ROWS, [needsSurface, rows, months]);
  const [preferredBasis, setBasis] = usePreference('options.holding.basis','daily');
  const basis = eod ? 'daily' : preferredBasis;
  const [previous, setPrevious] = useState(null);
  const [previousMessage, setPreviousMessage] = useState('正在读取上一截面…');
  useEffect(() => {
    if (eod) return;
    const controller = new AbortController();
    setPrevious(null); setPreviousMessage('正在读取上一截面…');
    fetch('/api/options/v1/previous', {signal:controller.signal}).then(async response => {
      if (!response.ok) throw new Error(await responseError(response, '上一截面读取失败'));
      return response.json();
    }).then(value => {
      if (!controller.signal.aborted) {
        setPrevious(value.snapshot);
        setPreviousMessage(value.snapshot ? '上一截面量仓不足' : '暂无上一截面');
      }
    }).catch(error => { if (!controller.signal.aborted) setPreviousMessage(error.message); });
    return () => controller.abort();
  }, [snapshot, eod]);
  const comparable = previous && previous.trade_date === snapshot.trade_date && Date.parse(previous.asof) < Date.parse(snapshot.asof) && previous.rows.some(row => Number.isFinite(row.oi) || Number.isFinite(row.volume));
  const comparableSnapshot = comparable ? previous : null;
  const monthRows = useMemo(() => rowsByMonth(rows), [rows]);
  const previousRows = useMemo(() => rowsByMonth(snapshot.previous_eod?.rows ?? NO_ROWS), [snapshot.previous_eod]);
  const scrollToMonth = useCallback(id => {
    setView('months');
    requestAnimationFrame(() => document.getElementById(`options-month-${id}`)?.scrollIntoView());
  }, [setView]);
  return <div className="options-analysis">
    {eod && <TermOverview surface={surface} previous={snapshot.previous_eod}
      date={snapshot.trade_date} onSelect={scrollToMonth} />}
    <div className="options-segment options-analysis-tools">
      {views.map(([id, label]) =>
        <button key={id} type="button" aria-pressed={view === id} onClick={() => setView(id)}>{label}</button>)}
    </div>
    {view === 'months' ? <>
      {!eod && <div className="options-analysis-tools"><label>变化基准<select value={basis} onChange={event => setBasis(event.target.value)}>
        <option value="daily">前一交易日</option><option value="snapshot" disabled={!comparable}>同日上一截面</option>
      </select></label>{basis === 'snapshot' && !comparable && <span className="options-muted">{previousMessage}</span>}</div>}
      <div className="options-month-grid">
        {months.map((month, index) => <MonthChart key={month.id} snapshot={snapshot} rows={monthRows.get(month.id) ?? NO_ROWS}
          previousRows={previousRows.get(month.id) ?? NO_ROWS} month={month} color={MONTH_COLORS[index % MONTH_COLORS.length]}
          basis={basis} comparable={comparableSnapshot} onOpenChain={onOpenChain} />)}
      </div>
    </> : <SurfaceComparison data={surface} metric={view} />}
  </div>;
}

function ChainCell({ value, formatter }) {
  return <td>{formatter ? formatter(value) : formatNumber(value)}</td>;
}

function ChainTab({ rows, month, months, spot, eod }) {
  const [search, setSearch] = usePreference('options.chain.search','');
  const [filter, setFilter] = usePreference('options.chain.filter','all');
  const monthRows = useMemo(() => getMonthRows(rows, month?.id), [rows, month?.id]);
  const groups = useMemo(() => groupRowsByStrike(monthRows), [monthRows]);
  const strikes = useMemo(() => [...groups.keys()].sort((a, b) => b - a), [groups]);
  const nearSet = useMemo(() => nearestStrikes(strikes, spot, 3), [strikes, spot]);
  const query = search.trim();
  const visibleStrikes = strikes.filter((strike) => {
    const group = groups.get(strike) || {};
    if (query && !String(strike).includes(query) && ![group.C?.code, group.P?.code].some(code => String(code || '').includes(query))) return false;
    return filter === 'all' || (filter === 'quoted' && (hasQuote(group.C) || hasQuote(group.P))) ||
      (filter === 'near' && nearSet.has(strike));
  });
  const code = month?.code || (month?.id ? `MO${String(month.id).slice(-4)}` : MISSING);
  const color = monthColor(months, month?.id);
  return (
    <>
      <div className="options-heading">
        <div>
          <h2>期权链</h2>
          <p className="options-muted"><ColoredCode code={code} color={color} /></p>
        </div>
      </div>
      <div className="options-chain-tools">
        <input aria-label="搜索执行价或合约" onChange={(event) => setSearch(event.target.value)} placeholder="搜索执行价，如 7700" type="search" value={search} />
        <div className="options-segment" aria-label="期权链筛选">
          {[['all', '全部'], ['quoted', eod ? '有收盘价' : '有报价'], ['near', '现货附近']].map(([value, label]) => <button aria-pressed={filter === value} key={value} onClick={() => setFilter(value)} type="button">{label}</button>)}
        </div>
        <span className="options-tool-note">{eod ? '价格' : '报价'}：点 · 持仓：手 · IV：%</span>
      </div>
      <section className="options-chain-wrap" tabIndex={0} aria-label="期权链明细">
        <table className="options-chain-table">
          <colgroup><col span={11} style={{ width: `${100 / 11}%` }} /></colgroup>
          <thead>
            <tr className="options-chain-group-head">
              <th colSpan="5"><span className="options-call-label">CALL · 看涨期权</span> <ColoredCode code={code} color={color} /></th>
              <th className="options-strike-head">执行价</th>
              <th colSpan="5"><span className="options-put-label">PUT · 看跌期权</span> <ColoredCode code={code} color={color} /></th>
            </tr>
            <tr>
              <th>OI</th><th>IV</th><th>Delta</th><th>{eod ? '成交量' : '卖价'}</th><th>{eod ? '收盘价' : '买价'}</th>
              <th className="options-strike-head">高 → 低</th>
              <th>{eod ? '收盘价' : '买价'}</th><th>{eod ? '成交量' : '卖价'}</th><th>Delta</th><th>IV</th><th>OI</th>
            </tr>
          </thead>
          <tbody>
            {visibleStrikes.map((strike) => {
              const group = groups.get(strike) || {};
              return (
                <tr key={strike}>
                  <ChainCell value={group.C?.oi} />
                  <ChainCell formatter={formatIv} value={group.C?.iv} />
                  <ChainCell formatter={formatDelta} value={group.C?.delta} />
                  <ChainCell formatter={eod ? formatNumber : formatPrice} value={eod ? group.C?.volume : group.C?.ask} />
                  <ChainCell formatter={formatPrice} value={eod ? group.C?.close : group.C?.bid} />
                  <td className="options-strike-cell">{formatPrice(strike,0)}</td>
                  <ChainCell formatter={formatPrice} value={eod ? group.P?.close : group.P?.bid} />
                  <ChainCell formatter={eod ? formatNumber : formatPrice} value={eod ? group.P?.volume : group.P?.ask} />
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
    </>
  );
}

export default function Options() {
  const [{snapshot, status, error}, setResource] = useState({status: 'loading'});
  const [attempt, setAttempt] = useState(0);
  const [selectedMonthId, setSelectedMonthId] = useState(() => new URLSearchParams(location.search).get('month'));
  const [activeTab, setActiveTab] = useState(() => {
    const tab = new URLSearchParams(location.search).get('tab');
    return tab === 'chain' ? 'chain' : 'analysis';
  });

  useEffect(() => {
    const controller = new AbortController();
    const {signal} = controller;
    setResource({status: 'loading'});
    async function loadSnapshot() {
      try {
        const response = await fetch(SNAPSHOT_ENDPOINT, {headers: {Accept: 'application/json'}, signal});
        if (!response.ok) {
          const empty = response.headers.get('X-Snapshot-State') === 'empty';
          const error = empty ? undefined : await responseError(response, '快照请求失败');
          if (!signal.aborted) setResource({status: empty ? 'empty' : 'error', error});
          return;
        }
        const snapshot = normalizeSnapshot(await response.json());
        if (signal.aborted) return;
        setResource({status: 'ready', snapshot});
        setSelectedMonthId(current => snapshot.months.some(month => month.id === current) ? current : (snapshot.months[0]?.id ?? null));
      } catch (error) {
        if (!signal.aborted) setResource({status: 'error', error: error.message || '无法读取快照'});
      }
    }
    loadSnapshot();
    return () => controller.abort();
  }, [attempt]);

  useEffect(() => {
    const url = new URL(location.href);
    url.searchParams.set('tab', activeTab);
    if (selectedMonthId) url.searchParams.set('month', selectedMonthId);
    else url.searchParams.delete('month');
    history.replaceState(null, '', url);
  }, [activeTab, selectedMonthId]);

  const months = snapshot?.months || [];
  const eod = snapshot?.mode === 'eod';
  const selectedMonth = months.find((month) => String(month.id) === String(selectedMonthId)) || months[0] || null;
  function selectTab(tab) {
    if (tab === activeTab) return;
    setActiveTab(tab);
    window.scrollTo(0, 0);
  }
  const content = status === 'loading' ? (
    <div className="options-loading"><span className="options-spinner" />正在读取期权快照…</div>
  ) : status === 'empty' ? (
    <EmptyState title="期权数据尚未接入" />
  ) : status === 'error' ? (
    <div className="options-error-state"><h2>快照读取失败</h2><p>{error}</p><button className="options-primary-button" onClick={() => setAttempt(value => value + 1)} type="button">重试</button></div>
  ) : !months.length ? (
    <EmptyState title="快照没有月份" />
  ) : (
    <>
      <div className="options-market-tools">
        <div className="options-quote-row"><span>中证1000 <strong>{formatPrice(snapshot.spot)}</strong></span></div>
        {activeTab === 'chain' && <>
          <MonthStrip months={months} onSelect={setSelectedMonthId} selectedMonthId={selectedMonth?.id} />
          <p className="options-note">{formatDte(selectedMonth)} / <time dateTime={selectedMonth?.expiry}>{formatDate(selectedMonth?.expiry)}</time></p>
        </>}
      </div>
      {activeTab === 'chain' ? <ChainTab months={months} rows={snapshot.rows} month={selectedMonth} spot={snapshot.spot} eod={eod} /> :
        <OptionsAnalysis snapshot={snapshot} onOpenChain={id => { setSelectedMonthId(id); selectTab('chain'); }} />}
    </>
  );

  return (
    <div className="options-module board-content">
      <div className="board-tabs" role="tablist" aria-label="期权分析页签">
        {TAB_ITEMS.map((tab) => <button aria-selected={activeTab === tab.id} key={tab.id} onClick={() => selectTab(tab.id)} role="tab" type="button">{tab.label}</button>)}
      </div>
      <main className="options-main" aria-busy={status === 'loading'}>{content}</main>
    </div>
  );
}
