import { conditionalFetch as fetch } from './transport.js';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useChart, Metric, usePreference, formatPrice as price } from './charts.js';
import './options.css';
import SurfaceComparison, { ColoredCode, MONTH_COLORS, TermOverview } from './SurfaceComparison.jsx';
import { surfaceMetrics } from './surfaceMetrics.js';
import { holdingRows, putCallRatio, optionSmile, optionStrikeExtent, optionStrikeTooltip, OPTION_IV_CURVE, OPTION_PRODUCTS, OPTION_SIDES, OPTION_STRIKE_TOOLTIP } from './holdings.js';

const SNAPSHOT_ENDPOINT = '/api/options/v1/snapshot';
const MISSING = '—';
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

function formatDate(value) {
  if (value === null || value === undefined || value === '') return MISSING;
  return String(value).slice(0, 10);
}

function formatDte(month) {
  return `${formatNumber(month?.trading_days, 0, false)} DTE`;
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

function nearestStrikes(strikes, spot, count) {
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

function MonthChart({ snapshot, rows, previousRows, month, color, basis, comparable, open, onDetailsChange }) {
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
    const series = [...(hasIv ? iv : []), ...holding];
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
      xAxis: {type: 'value', ...optionStrikeExtent(series), axisPointer: {snap: true}, splitLine: {show: false}, splitNumber: 5,
        axisLabel: {hideOverlap: true, showMinLabel: false, showMaxLabel: false, color: '#455161', fontSize: 11, formatter: value => price(value)},
        axisLine: {show: false, onZero: false}, axisTick: {show: false}},
      yAxis: [
        {...verticalAxis, position: 'left', show: hasIv, scale: true, splitNumber: 4,
          axisLabel: {color: '#455161', fontSize: 11, formatter: '{value}%'}},
        {...verticalAxis, position: 'right', show: hasHolding, splitNumber: 4,
          min: ({min, max}) => Math.max(Math.min(min, 0), -0.2 * Math.max(max, 1)),
          axisLabel: {color: '#455161', fontSize: 11, showMinLabel: !reduced, formatter: value => value === 0 ? '0 手' : formatNumber(value)}},
      ],
      series,
    }, true);
  }, [groups, points, previousPoints, hasPrevious, hasIv, hasHolding, hasOi, showVolume, date, prior, spot]);

  return <section className="options-month-panel" id={`options-month-${month.id}`}>
    <div className="options-month-heading">
      <h2><ColoredCode code={month.code} color={color} /></h2>
      <span className="options-muted">{formatDte(month)} / <time dateTime={month.expiry}>{formatDate(month.expiry)}</time></span>
      <button className="options-plain-button" type="button" aria-expanded={open}
        aria-controls={`options-details-${month.id}`} onClick={() => onDetailsChange(open ? null : month.id)}>
        {eod ? '收盘明细' : '截面明细'}
      </button>
    </div>
    <div className="options-pcr">{pcr.map((value, index) => <Metric key={index} label={(index ? '成交' : '持仓') + ' PCR' + (snapshot.test_only ? ' · 样本' : '')}
      value={formatNumber(value.ratio, 2, false)} hint={value.reason || undefined}/>)}</div>
    {hasIv || hasHolding ? <div className="options-month-chart" ref={chartRef}
      role="img" aria-label={`${month.code} 隐波、持仓与成交`} /> : <EmptyState title="暂无数据" />}
    <div id={`options-details-${month.id}`} className="options-strike-details" hidden={!open}>
      {open && <StrikeDetails rows={rows} reference={reference ?? spot} code={month.code} />}
    </div>
  </section>;
}

function OptionsAnalysis({ snapshot, detailsMonth, onDetailsChange }) {
  const {rows, months} = snapshot;
  const [preferredView, setView] = usePreference('options.analysis.view', 'months');
  const eod = snapshot.mode === 'eod';
  const views = eod ? SURFACE_VIEWS.slice(0, 2) : SURFACE_VIEWS;
  const view = !detailsMonth && views.some(([id]) => id === preferredView) ? preferredView : 'months';
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
        <button key={id} type="button" aria-pressed={view === id} onClick={() => {
          if (id !== 'months') onDetailsChange(null);
          setView(id);
        }}>{label}</button>)}
    </div>
    {view === 'months' ? <>
      {!eod && <div className="options-analysis-tools"><label>变化基准<select value={basis} onChange={event => setBasis(event.target.value)}>
        <option value="daily">前一交易日</option><option value="snapshot" disabled={!comparable}>同日上一截面</option>
      </select></label>{basis === 'snapshot' && !comparable && <span className="options-muted">{previousMessage}</span>}</div>}
      <div className="options-month-grid">
        {months.map((month, index) => <MonthChart key={month.id} snapshot={snapshot} rows={monthRows.get(month.id) ?? NO_ROWS}
          previousRows={previousRows.get(month.id) ?? NO_ROWS} month={month} color={MONTH_COLORS[index % MONTH_COLORS.length]}
          basis={basis} comparable={comparableSnapshot} open={detailsMonth === month.id} onDetailsChange={id => { setView('months'); onDetailsChange(id); }} />)}
      </div>
    </> : <SurfaceComparison data={surface} metric={view} />}
  </div>;
}

function PositionChange({ value }) {
  return <td className={value > 0 ? 'options-up' : value < 0 ? 'options-down' : undefined}>
    {value > 0 ? '+' : ''}{formatNumber(value)}
  </td>;
}

function StrikeDetails({ rows, reference, code }) {
  const [search, setSearch] = useState('');
  const [showAll, setAll] = useState(false);
  const groups = useMemo(() => groupRowsByStrike(rows), [rows]);
  const strikes = useMemo(() => [...groups.keys()].sort((a, b) => a - b), [groups]);
  const near = useMemo(() => nearestStrikes(strikes, reference, 7), [strikes, reference]);
  const query = search.trim().toUpperCase();
  const visible = strikes.filter(strike => {
    if (!query) return showAll || !near.size || near.has(strike);
    const { C, P } = groups.get(strike);
    return String(strike).includes(query) || C?.code?.toUpperCase().includes(query) || P?.code?.toUpperCase().includes(query);
  }).reverse();

  return <>
    <div className="options-detail-tools">
      <input aria-label="搜索行权价或合约" placeholder="搜索行权价或合约" type="search"
        value={search} onChange={event => setSearch(event.target.value)} />
      <div className="options-segment" role="group" aria-label="行权价范围">
        {[[false, '平值附近'], [true, '全部']].map(([all, label]) =>
          <button key={label} type="button" aria-pressed={!query && showAll === all}
            onClick={() => { setSearch(''); setAll(all); }}>{label}</button>)}
      </div>
    </div>
    <div className="options-detail-scroll" role="region" tabIndex={0} aria-label={`${code} 行权价明细`}>
      <table className="options-detail-table">
        <colgroup span={4} /><colgroup span={1} /><colgroup span={4} />
        <thead>
          <tr>
            <th scope="colgroup" colSpan={4}>认购</th>
            <th scope="col" rowSpan={2} aria-sort="descending" className="options-strike-cell">行权价</th>
            <th scope="colgroup" colSpan={4}>认沽</th>
          </tr>
          <tr>
            <th scope="col">持仓</th><th scope="col">日增减</th><th scope="col">IV</th><th scope="col" aria-label="Delta">Δ</th>
            <th scope="col" aria-label="Delta">Δ</th><th scope="col">IV</th><th scope="col">日增减</th><th scope="col">持仓</th>
          </tr>
        </thead>
        <tbody>
          {visible.map(strike => {
            const { C, P } = groups.get(strike);
            return <tr key={strike}>
              <td>{formatNumber(C?.oi)}</td>
              <PositionChange value={C?.oi_change} />
              <td>{formatIv(C?.iv)}</td>
              <td>{formatNumber(C?.delta, 2, false)}</td>
              <th scope="row" className="options-strike-cell">{formatPrice(strike, 0)}</th>
              <td>{formatNumber(P?.delta, 2, false)}</td>
              <td>{formatIv(P?.iv)}</td>
              <PositionChange value={P?.oi_change} />
              <td>{formatNumber(P?.oi)}</td>
            </tr>;
          })}
        </tbody>
      </table>
      {!visible.length && <p className="options-detail-empty">没有匹配的行权价</p>}
    </div>
  </>;
}

export default function Options() {
  const [product, setProduct] = useState(() => {
    const requested = new URLSearchParams(location.search).get('product');
    return Object.hasOwn(OPTION_PRODUCTS, requested) ? requested : 'MO';
  });
  const [resource, setResource] = useState({});
  const [attempt, setAttempt] = useState(0);
  const {snapshot, status = 'loading', error} =
    resource.product === product && resource.attempt === attempt ? resource : {};
  const [detailsMonth, setDetailsMonth] = useState(() => {
    const params = new URLSearchParams(location.search);
    return params.get('details') ?? (params.get('tab') === 'chain' ? params.get('month') ?? '' : null);
  });
  const jumpToDetails = useRef(detailsMonth !== null);

  useEffect(() => {
    const controller = new AbortController();
    const {signal} = controller;
    const endpoint = product === 'MO' ? SNAPSHOT_ENDPOINT : SNAPSHOT_ENDPOINT + '/' + product;
    const publish = result => { if (!signal.aborted) setResource({product, attempt, ...result}); };
    async function loadSnapshot() {
      try {
        const response = await fetch(endpoint, {headers: {Accept: 'application/json'}, signal});
        if (!response.ok) {
          const empty = response.headers.get('X-Snapshot-State') === 'empty';
          const error = empty ? undefined : await responseError(response, '快照请求失败');
          publish({status: empty ? 'empty' : 'error', error});
          return;
        }
        const snapshot = normalizeSnapshot(await response.json());
        if (signal.aborted) return;
        publish({status: 'ready', snapshot});
        setDetailsMonth(current => current === null || snapshot.months.some(month => month.id === current)
          ? current : snapshot.months[0]?.id ?? null);
      } catch (error) {
        publish({status: 'error', error: error.message || '无法读取快照'});
      }
    }
    loadSnapshot();
    return () => controller.abort();
  }, [product, attempt]);

  useEffect(() => {
    const url = new URL(location.href);
    url.searchParams.delete('tab');
    url.searchParams.delete('month');
    if (product === 'MO') url.searchParams.delete('product');
    else url.searchParams.set('product', product);
    if (detailsMonth === null) url.searchParams.delete('details');
    else url.searchParams.set('details', detailsMonth);
    history.replaceState(history.state, '', url);
  }, [product, detailsMonth]);

  useEffect(() => {
    if (!jumpToDetails.current || !snapshot || !detailsMonth) return;
    const frame = requestAnimationFrame(() => {
      jumpToDetails.current = false;
      document.getElementById(`options-details-${detailsMonth}`)?.scrollIntoView({block: 'nearest'});
    });
    return () => cancelAnimationFrame(frame);
  }, [snapshot, detailsMonth]);

  function selectProduct(code) {
    if (code === product) return;
    jumpToDetails.current = false;
    setDetailsMonth(null);
    setProduct(code);
  }
  const content = status === 'loading' ? (
    <div className="options-loading"><span className="options-spinner" />正在读取期权快照…</div>
  ) : status === 'empty' ? (
    <EmptyState title="期权数据尚未接入" />
  ) : status === 'error' ? (
    <div className="options-error-state"><h2>快照读取失败</h2><p>{error}</p><button className="options-primary-button" onClick={() => setAttempt(value => value + 1)} type="button">重试</button></div>
  ) : !snapshot.months.length ? (
    <EmptyState title="快照没有月份" />
  ) : (
    <OptionsAnalysis snapshot={snapshot} detailsMonth={detailsMonth} onDetailsChange={setDetailsMonth} />
  );

  return (
    <div className="options-module board-content">
      <main className="options-main">
        <div className="options-market-tools">
          <div className="options-months" role="group" aria-label="股指期权品种">
            {Object.entries(OPTION_PRODUCTS).map(([code, name]) => (
              <button key={code} type="button" aria-pressed={code === product}
                className={'options-month' + (code === product ? ' options-month-selected' : '')}
                onClick={() => selectProduct(code)}>{name}</button>
            ))}
          </div>
          <strong className="options-index-price">{snapshot ? formatPrice(snapshot.spot) : '—'}</strong>
        </div>
        <div aria-busy={status === 'loading'}>{content}</div>
      </main>
    </div>
  );
}
