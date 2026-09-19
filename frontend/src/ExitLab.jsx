import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as echarts from 'echarts';
import { buildCandidates } from './candidates.js';
import './exit-lab.css';

const EXIT_ENDPOINT = '/api/options/v1/exit-analysis';
const HISTORY_ENDPOINT = '/api/options/v1/history';
const CONFIG_STORAGE_KEY = 'options.exitLab.v1';
const CURRENT_PORTFOLIO = 'current-basket';
const MISSING = '—';
const MONTH_COLORS = ['#3274d2', '#8b5dc5', '#38878c', '#af8033', '#6f7ec0', '#a66587'];

function toNumber(value) {
  if (value === null || value === undefined || value === '') return null;
  const number = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(number) ? number : null;
}

function inputNumber(value, fallback = '') {
  const number = toNumber(value);
  if (number === null) return fallback;
  return Number.isInteger(number) ? String(number) : String(Number(number.toFixed(4)));
}

function formatNumber(value, digits = 0) {
  const number = toNumber(value);
  if (number === null) return MISSING;
  return number.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatPnl(value, digits = 0) {
  const number = toNumber(value);
  if (number === null) return MISSING;
  return `${number > 0 ? '+' : ''}${formatNumber(number, digits)}`;
}

function pnlClass(value) {
  const number = toNumber(value);
  if (number === null || number === 0) return 'exit-lab-neutral';
  return number > 0 ? 'exit-lab-profit' : 'exit-lab-loss';
}

function costClass(value) {
  const number = toNumber(value);
  if (number === null || number === 0) return 'exit-lab-neutral';
  return 'exit-lab-loss';
}

function mixColor(start, end, amount) {
  const parse = (color) => color.match(/[\da-f]{2}/gi).map((part) => parseInt(part, 16));
  const from = parse(start);
  const to = parse(end);
  const channel = (index) => Math.round(from[index] + (to[index] - from[index]) * amount).toString(16).padStart(2, '0');
  return `#${channel(0)}${channel(1)}${channel(2)}`;
}

function heatmapColor(value, scale) {
  const number = toNumber(value);
  if (number === null || number === 0) return '#e6ebf1';
  const intensity = Math.min(1, Math.abs(number) / Math.max(scale, 1));
  const amount = 0.25 + intensity * 0.75;
  return number > 0 ? mixColor('#f3d9dc', '#b52e38', amount) : mixColor('#d8eee7', '#14765f', amount);
}

function formatDate(value) {
  if (value === null || value === undefined || value === '') return MISSING;
  return String(value).replace('T', ' ').slice(0, 19);
}

function formatExitDate(asof, days) {
  const start = Date.parse(String(asof || ''));
  const dayNumber = toNumber(days);
  if (!Number.isFinite(start) || dayNumber === null) return MISSING;
  return new Intl.DateTimeFormat('sv-SE', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(start + dayNumber * 86400000);
}

function monthColor(monthId, fallback = MONTH_COLORS[0]) {
  const parsed = Number(String(monthId ?? '').replace(/\D/g, ''));
  return Number.isFinite(parsed) ? MONTH_COLORS[Math.abs(parsed) % MONTH_COLORS.length] : fallback;
}

function ColoredCode({ code, color }) {
  if (code === null || code === undefined || code === '') return <span className="exit-lab-code">{MISSING}</span>;
  const text = String(code);
  const match = text.match(/^([A-Za-z]+)(\d{4})(.*)$/);
  if (!match) return <span className="exit-lab-code">{text}</span>;
  return (
    <span className="exit-lab-code">
      {match[1]}{match[2].slice(0, 2)}<span style={{ color }}>{match[2].slice(2)}</span>{match[3]}
    </span>
  );
}

function normalizeLeg(leg) {
  if (!leg || typeof leg !== 'object' || !String(leg.code || '').trim()) return null;
  const quantity = toNumber(leg.quantity);
  return {
    code: String(leg.code).trim(),
    side: leg.side === 'sell' ? 'sell' : 'buy',
    quantity: Math.max(1, Math.min(10000, Math.trunc(quantity === null ? 1 : quantity))),
  };
}

function normalizeLegs(legs) {
  return Array.isArray(legs) ? legs.map(normalizeLeg).filter(Boolean) : [];
}

function normalizeSnapshot(value) {
  if (!value || typeof value !== 'object') return null;
  return {
    ...value,
    months: Array.isArray(value.months) ? value.months.filter(Boolean).map((item) => ({ ...item, id: String(item.id ?? '') })) : [],
    rows: Array.isArray(value.rows) ? value.rows.filter(Boolean).map((item) => ({ ...item, month: String(item.month ?? '') })) : [],
    warnings: Array.isArray(value.warnings) ? value.warnings : [],
  };
}

function readSavedConfig() {
  const fallback = {
    fee_roundtrip: '0',
    iv_shift_pp: '0',
    profit_target: '5000',
  };
  if (typeof window === 'undefined') return fallback;
  try {
    const parsed = JSON.parse(window.localStorage.getItem(CONFIG_STORAGE_KEY) || '{}');
    return Object.keys(fallback).reduce((result, key) => {
      result[key] = toNumber(parsed?.[key]) === null ? fallback[key] : inputNumber(parsed[key], fallback[key]);
      return result;
    }, { ...fallback });
  } catch {
    return fallback;
  }
}

function flattenDetail(value) {
  if (value === null || value === undefined || value === '') return '';
  if (typeof value === 'string' || typeof value === 'number') return String(value);
  if (Array.isArray(value)) return value.map(flattenDetail).filter(Boolean).join('；');
  if (typeof value === 'object') {
    return String(value.message || value.msg || value.detail || value.error || JSON.stringify(value));
  }
  return String(value);
}

async function responseError(response, fallback) {
  let detail = '';
  try {
    const payload = await response.clone().json();
    detail = flattenDetail(payload?.detail ?? payload?.message ?? payload?.error);
  } catch {
    // Some gateway errors are plain text; the status below remains useful.
  }
  const suffix = detail || `HTTP ${response.status}`;
  return `${fallback}：${suffix}`;
}

function defaultHorizonDays(legs, rows) {
  const rowsByCode = new Map((Array.isArray(rows) ? rows : []).map((row) => [String(row.code), row]));
  const remaining = normalizeLegs(legs)
    .map((leg) => toNumber(rowsByCode.get(leg.code)?.t))
    .filter((days) => days !== null && days >= 0)
    .map((years) => years * 365);
  if (!remaining.length) return 7;
  return Math.max(0, Math.min(7, ...remaining));
}

function legText(leg) {
  return `${leg.side === 'sell' ? '卖' : '买'} ${formatNumber(leg.quantity)} 手`;
}

function PortfolioLegs({ legs, monthId, emptyText = '暂无可用腿' }) {
  if (!legs.length) return <p className="exit-lab-empty-inline">{emptyText}</p>;
  return (
    <div className="exit-lab-leg-list">
      {legs.map((leg, index) => (
        <div className="exit-lab-leg" key={`${leg.code}-${leg.side}-${index}`}>
          <ColoredCode code={leg.code} color={monthColor(leg.code.match(/^[A-Za-z]+(\d{4})/)?.[1] || monthId)} />
          <span className={leg.side === 'sell' ? 'exit-lab-side-sell' : 'exit-lab-side-buy'}>{legText(leg)}</span>
        </div>
      ))}
    </div>
  );
}

function PnlCell({ value, digits = 0, title }) {
  return <span className={pnlClass(value)} title={title}>{formatPnl(value, digits)}</span>;
}

function Heatmap({ result, displayMode, asof }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);

  useEffect(() => {
    if (!containerRef.current) return undefined;
    const chart = echarts.init(containerRef.current, null, { renderer: 'canvas' });
    chartRef.current = chart;
    const resize = () => chart.resize();
    window.addEventListener('resize', resize);
    let observer;
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(resize);
      observer.observe(containerRef.current);
    }
    return () => {
      observer?.disconnect();
      window.removeEventListener('resize', resize);
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  const heatmapModel = useMemo(() => {
    const prices = Array.isArray(result?.prices) ? result.prices.map(toNumber) : [];
    const days = Array.isArray(result?.days) ? result.days.map(toNumber) : [];
    const cells = Array.isArray(result?.cells) ? result.cells : [];
    const key = displayMode === 'stress' ? 'stress_pnl' : 'pnl';
    const allValues = cells.flatMap((cell) => [toNumber(cell?.pnl), toNumber(cell?.stress_pnl)]).filter((value) => value !== null);
    const sharedScale = allValues.length ? Math.max(...allValues.map((value) => Math.abs(value)), 1) : 1;
    const data = cells.reduce((items, cell) => {
      const x = Math.trunc(toNumber(cell?.day_index));
      const y = Math.trunc(toNumber(cell?.price_index));
      const value = toNumber(cell?.[key]);
      if (!Number.isFinite(x) || !Number.isFinite(y) || value === null || days[x] === null || prices[y] === null) return items;
      items.push({
        value: [x, y, value],
        itemStyle: { color: heatmapColor(value, sharedScale) },
      });
      return items;
    }, []);
    const lookup = new Map(cells.map((cell) => [`${cell?.price_index}:${cell?.day_index}`, cell]));
    return { prices, days, data, lookup, sharedScale };
  }, [displayMode, result]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const { prices, days, data, lookup, sharedScale } = heatmapModel;
    const priceLabels = prices.map((price) => formatNumber(price, 1));
    const dayLabels = days.map((day) => `${formatNumber(day, 2)}天`);
    chart.setOption({
      animation: false,
      grid: { left: 59, right: 17, top: 17, bottom: 62 },
      visualMap: {
        show: false,
        dimension: 2,
        min: -sharedScale,
        max: sharedScale,
        inRange: { color: ['#16866d', '#e6ebf1', '#ce414a'] },
      },
      tooltip: {
        trigger: 'item',
        confine: true,
        formatter(params) {
          const value = Array.isArray(params?.value) ? params.value : [];
          const x = Math.trunc(toNumber(value[0]));
          const y = Math.trunc(toNumber(value[1]));
          const cell = lookup.get(`${y}:${x}`) || {};
          const price = prices[y];
          const day = days[x];
          const cellError = cell.error ? `<div class="exit-lab-tooltip-error">${String(cell.error)}</div>` : '';
          return [
            `<strong>${formatExitDate(asof, day)}</strong> · ${formatNumber(day, 2)}天`,
            `价格 ${formatNumber(price, 1)}`,
            `普通损益 <span class="${pnlClass(cell.pnl)}">${formatPnl(cell.pnl)}</span>`,
            `保守损益 <span class="${pnlClass(cell.stress_pnl)}">${formatPnl(cell.stress_pnl)}</span>`,
            cellError,
          ].filter(Boolean).join('<br />');
        },
      },
      xAxis: {
        type: 'category',
        data: dayLabels,
        name: '经过天数',
        nameLocation: 'middle',
        nameGap: 40,
        axisLine: { lineStyle: { color: '#cbd5e1' } },
        axisLabel: { color: '#78869a', rotate: dayLabels.length > 9 ? 35 : 0 },
        splitArea: { show: false },
      },
      yAxis: {
        type: 'category',
        data: priceLabels,
        inverse: false,
        name: '目标指数',
        nameLocation: 'middle',
        nameGap: 43,
        axisLine: { lineStyle: { color: '#cbd5e1' } },
        axisLabel: { color: '#78869a' },
      },
      series: [{
        type: 'heatmap',
        data,
        progressive: 0,
        label: { show: false },
        emphasis: { itemStyle: { borderColor: '#243248', borderWidth: 1 } },
      }],
    }, true);
    chart.resize();
  }, [asof, heatmapModel]);

  return (
    <div className="exit-lab-chart-wrap">
      <div ref={containerRef} className="exit-lab-heatmap" role="img" aria-label={`退出损益热图，${displayMode === 'stress' ? '保守' : '普通'}退出价`} />
      {!heatmapModel.data.length ? <div className="exit-lab-chart-empty">当前切换下没有可绘制的数值；空值保持为空。</div> : null}
    </div>
  );
}

function EntrySummary({ entry }) {
  const values = [
    ['入场成本', entry?.cost, 'neutral'],
    ['到期最大亏损', entry?.max_loss, 'loss'],
    ['到期最大收益', entry?.max_gain, 'profit'],
  ];
  const riskText = (value) => {
    if (toNumber(value) !== null) return formatNumber(value, 0);
    if (entry?.risk_status === 'same_expiry') return '无上限';
    if (entry?.risk_status === 'cross_expiry_unresolved') return '待评估';
    return MISSING;
  };
  return (
    <div className="exit-lab-entry-grid">
      {values.map(([label, value, kind]) => (
        <div className="exit-lab-entry-card" key={label}>
          <span>{label}</span>
          <strong className={kind === 'profit' ? 'exit-lab-profit' : kind === 'loss' ? costClass(value) : 'exit-lab-neutral'}>{riskText(value)}</strong>
          {toNumber(value) !== null ? <small>元</small> : null}
        </div>
      ))}
    </div>
  );
}

function TargetTable({ result, asof }) {
  const rows = Array.isArray(result?.target_rows) ? result.target_rows : [];
  return (
    <section className="exit-lab-table-section">
      <div className="exit-lab-subheading">
        <div>
          <h3>到达目标时退出</h3>
          <p>目标指数固定为表单中的目标点位；普通/保守损益均绑定同一快照。</p>
        </div>
      </div>
      <div className="exit-lab-table-wrap">
        <table className="exit-lab-table">
          <thead><tr><th>持有</th><th>退出日期</th><th>普通净损益</th><th>保守净损益</th></tr></thead>
          <tbody>
            {rows.length ? rows.map((row, index) => {
              const normal = row?.normal;
              const stress = row?.stress;
              return (
                <tr key={`${row?.days}-${index}`}>
                  <td>{formatNumber(row?.days, 2)}天</td>
                  <td>{formatExitDate(asof, row?.days)}</td>
                  <td><PnlCell value={normal?.pnl} title={normal ? `基准损益（已含普通买卖价差） ${formatPnl(normal.gross_pnl)}；额外摩擦 ${formatNumber(normal.friction_cost)}` : undefined} /></td>
                  <td><PnlCell value={stress?.pnl} title={stress ? `基准损益（已含普通买卖价差） ${formatPnl(stress.gross_pnl)}；额外摩擦 ${formatNumber(stress.friction_cost)}` : undefined} /></td>
                </tr>
              );
            }) : <tr><td colSpan="4" className="exit-lab-table-empty">接口没有返回目标情景。</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ReverseTable({ result, asof }) {
  const rows = Array.isArray(result?.reverse_rows) ? result.reverse_rows : [];
  const priceText = (value) => value === null || value === undefined || value === '' ? '范围内未达到' : formatNumber(value, 1);
  return (
    <section className="exit-lab-table-section">
      <div className="exit-lab-subheading">
        <div>
          <h3>达到盈利目标所需价格</h3>
          <p>价格是扫描网格中首个达到盈利目标的点位，不代表精确根，也不代表触达概率。</p>
        </div>
      </div>
      <div className="exit-lab-table-wrap">
        <table className="exit-lab-table">
          <thead><tr><th>持有</th><th>退出日期</th><th>普通退出价</th><th>保守退出价</th></tr></thead>
          <tbody>
            {rows.length ? rows.map((row, index) => (
              <tr key={`${row?.days}-${index}`}>
                <td>{formatNumber(row?.days, 2)}天</td>
                <td>{formatExitDate(asof, row?.days)}</td>
                <td className={row?.normal_price === null ? 'exit-lab-unreached' : ''}>{priceText(row?.normal_price)}</td>
                <td className={row?.stress_price === null ? 'exit-lab-unreached' : ''}>{priceText(row?.stress_price)}</td>
              </tr>
            )) : <tr><td colSpan="4" className="exit-lab-table-empty">接口没有返回反推价格。</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default function ExitLab({ snapshot, month, basket, target }) {
  const normalizedCurrentSnapshot = useMemo(() => normalizeSnapshot(snapshot), [snapshot]);
  const saved = useMemo(() => readSavedConfig(), []);
  const [feeRoundtrip, setFeeRoundtrip] = useState(() => saved.fee_roundtrip);
  const [ivShift, setIvShift] = useState(() => saved.iv_shift_pp);
  const [profitTarget, setProfitTarget] = useState(() => saved.profit_target);
  const [targetInput, setTargetInput] = useState(() => inputNumber(target, inputNumber(snapshot?.spot, '')));
  const [horizonDays, setHorizonDays] = useState('7');
  const [priceMin, setPriceMin] = useState('');
  const [priceMax, setPriceMax] = useState('');
  const [portfolioId, setPortfolioId] = useState('');
  const [displayMode, setDisplayMode] = useState('normal');
  const [historyKey, setHistoryKey] = useState('');
  const [history, setHistory] = useState({ status: 'idle', items: [], currentKey: '', error: '' });
  const [historySnapshot, setHistorySnapshot] = useState(null);
  const [historyLoad, setHistoryLoad] = useState({ status: 'idle', error: '' });
  const [analysis, setAnalysis] = useState({ status: 'idle', result: null, error: '' });
  const analysisRequestRef = useRef(0);
  const analysisControllerRef = useRef(null);

  const invalidateAnalysis = useCallback(() => {
    analysisRequestRef.current += 1;
    analysisControllerRef.current?.abort();
    analysisControllerRef.current = null;
    setAnalysis({ status: 'idle', result: null, error: '' });
  }, []);

  useEffect(() => {
    if (target !== null && target !== undefined && target !== '') {
      invalidateAnalysis();
      setTargetInput(inputNumber(target, ''));
    }
  }, [invalidateAnalysis, target]);

  useEffect(() => {
    try {
      window.localStorage.setItem(CONFIG_STORAGE_KEY, JSON.stringify({
        fee_roundtrip: feeRoundtrip,
        iv_shift_pp: ivShift,
        profit_target: profitTarget,
      }));
    } catch {
      // The controls remain usable when storage is unavailable.
    }
  }, [feeRoundtrip, ivShift, profitTarget]);

  useEffect(() => {
    const snapshotId = normalizedCurrentSnapshot?.snapshot_id;
    if (!snapshotId) return undefined;
    setHistoryKey('');
    setHistorySnapshot(null);
    setHistoryLoad({ status: 'idle', error: '' });
    invalidateAnalysis();
    const controller = new AbortController();
    let active = true;
    setHistory((current) => ({ ...current, status: 'loading', error: '' }));
    fetch(HISTORY_ENDPOINT, { cache: 'no-store', headers: { Accept: 'application/json' }, signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(await responseError(response, '历史快照列表读取失败'));
        const payload = await response.json();
        if (!active || controller.signal.aborted) return null;
        return {
          items: Array.isArray(payload?.items) ? payload.items.filter((item) => item && item.key !== undefined).map((item) => ({ ...item, key: String(item.key) })) : [],
          currentKey: payload?.current_key === undefined || payload?.current_key === null ? '' : String(payload.current_key),
        };
      })
      .then((payload) => {
        if (!payload || !active || controller.signal.aborted) return;
        setHistory({ status: 'ready', ...payload, error: '' });
      })
      .catch((error) => {
        if (error?.name === 'AbortError' || !active || controller.signal.aborted) return;
        setHistory((current) => ({ ...current, status: 'error', error: error?.message || '历史快照列表读取失败' }));
      });
    return () => { active = false; controller.abort(); };
  }, [invalidateAnalysis, normalizedCurrentSnapshot?.snapshot_id]);

  useEffect(() => {
    if (!historyKey) {
      setHistorySnapshot(null);
      setHistoryLoad({ status: 'idle', error: '' });
      return undefined;
    }
    const controller = new AbortController();
    let active = true;
    setHistoryLoad({ status: 'loading', error: '' });
    fetch(`${HISTORY_ENDPOINT}/${encodeURIComponent(historyKey)}`, { cache: 'no-store', headers: { Accept: 'application/json' }, signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(await responseError(response, '历史快照读取失败'));
        const payload = await response.json();
        if (!active || controller.signal.aborted) return null;
        return normalizeSnapshot(payload);
      })
      .then((next) => {
        if (!next || !active || controller.signal.aborted) return;
        setHistorySnapshot(next);
        setHistoryLoad({ status: 'ready', error: '' });
      })
      .catch((error) => {
        if (error?.name === 'AbortError' || !active || controller.signal.aborted) return;
        setHistorySnapshot(null);
        setHistoryLoad({ status: 'error', error: error?.message || '历史快照读取失败' });
      });
    return () => { active = false; controller.abort(); };
  }, [historyKey]);

  const activeSnapshot = historyKey ? historySnapshot : normalizedCurrentSnapshot;
  const activeMonth = useMemo(() => {
    if (!activeSnapshot?.months?.length) return month || null;
    return activeSnapshot.months.find((item) => String(item.id) === String(month?.id)) || activeSnapshot.months[0];
  }, [activeSnapshot, month]);
  const currentBasket = useMemo(() => normalizeLegs(basket), [basket]);
  const candidateList = useMemo(() => {
    if (!activeSnapshot || !activeMonth) return [];
    try {
      return buildCandidates(activeSnapshot.rows || [], activeMonth.id, toNumber(activeSnapshot.spot), toNumber(targetInput)) || [];
    } catch {
      return [];
    }
  }, [activeMonth, activeSnapshot, targetInput]);
  const portfolioOptions = useMemo(() => {
    const candidates = candidateList.map((candidate) => ({
      id: String(candidate.id),
      name: candidate.name,
      description: candidate.description,
      legs: normalizeLegs(candidate.legs),
    }));
    if (!currentBasket.length) return candidates;
    return [{ id: CURRENT_PORTFOLIO, name: '当前比较篮', description: '沿用全局比较篮；历史回看只在本页局部使用。', legs: currentBasket }, ...candidates];
  }, [candidateList, currentBasket]);
  const selectedPortfolio = portfolioOptions.find((item) => item.id === portfolioId) || portfolioOptions[0] || null;
  const selectedLegs = selectedPortfolio?.legs || [];
  const portfolioSignature = portfolioOptions.map((item) => `${item.id}:${item.legs.map((leg) => `${leg.code}/${leg.side}/${leg.quantity}`).join(',')}`).join('|');
  const basketSignature = currentBasket.map((leg) => `${leg.code}/${leg.side}/${leg.quantity}`).join(',');
  const structureSignature = `${activeSnapshot?.snapshot_id || ''}|${activeMonth?.id || ''}|${basketSignature}`;

  useEffect(() => {
    invalidateAnalysis();
    const fallbackPortfolio = currentBasket.length ? CURRENT_PORTFOLIO : candidateList[0]?.id || '';
    setPortfolioId(fallbackPortfolio);
    const fallbackLegs = currentBasket.length ? currentBasket : candidateList[0]?.legs || [];
    setHorizonDays(inputNumber(defaultHorizonDays(fallbackLegs, activeSnapshot?.rows), '7'));
  }, [invalidateAnalysis, structureSignature]);

  useEffect(() => {
    if (portfolioId && portfolioOptions.some((item) => item.id === portfolioId)) return;
    const fallbackPortfolio = currentBasket.length ? CURRENT_PORTFOLIO : candidateList[0]?.id || '';
    const fallbackLegs = currentBasket.length ? currentBasket : candidateList[0]?.legs || [];
    invalidateAnalysis();
    setPortfolioId(fallbackPortfolio);
    setHorizonDays(inputNumber(defaultHorizonDays(fallbackLegs, activeSnapshot?.rows), '7'));
  }, [activeSnapshot?.rows, candidateList, currentBasket, invalidateAnalysis, portfolioId, portfolioOptions, portfolioSignature]);

  useEffect(() => {
    const spot = toNumber(activeSnapshot?.spot);
    if (spot === null || spot <= 0) return;
    setPriceMin(inputNumber(spot * 0.95));
    setPriceMax(inputNumber(spot * 1.05));
  }, [activeSnapshot?.snapshot_id, activeSnapshot?.spot, historyKey]);

  useEffect(() => () => {
    analysisControllerRef.current?.abort();
  }, []);

  const updateField = (setter) => (event) => {
    invalidateAnalysis();
    setter(event.target.value);
  };

  const choosePortfolio = (event) => {
    invalidateAnalysis();
    const next = event.target.value;
    setPortfolioId(next);
    const selected = portfolioOptions.find((item) => item.id === next);
    if (selected) setHorizonDays(inputNumber(defaultHorizonDays(selected.legs, activeSnapshot?.rows), '7'));
  };

  const chooseHistory = (event) => {
    invalidateAnalysis();
    setHistoryKey(event.target.value);
    setHistorySnapshot(null);
    setHistoryLoad(event.target.value ? { status: 'loading', error: '' } : { status: 'idle', error: '' });
  };

  const runAnalysis = async () => {
    const requestId = analysisRequestRef.current + 1;
    analysisRequestRef.current = requestId;
    analysisControllerRef.current?.abort();
    if (!activeSnapshot?.snapshot_id) {
      setAnalysis({ status: 'error', result: null, error: '当前分析快照缺少唯一标识，暂不能计算' });
      return;
    }
    const values = {
      priceMin: toNumber(priceMin),
      priceMax: toNumber(priceMax),
      horizonDays: toNumber(horizonDays),
      ivShift: toNumber(ivShift),
      target: toNumber(targetInput),
      profitTarget: toNumber(profitTarget),
      feeRoundtrip: toNumber(feeRoundtrip),
    };
    if (!selectedLegs.length) {
      setAnalysis({ status: 'error', result: null, error: '请先选择一个候选组合或加入当前比较篮。' });
      return;
    }
    if (values.priceMin === null || values.priceMax === null || values.priceMin <= 0 || values.priceMax <= values.priceMin) {
      setAnalysis({ status: 'error', result: null, error: '价格范围必须为正数，且上限大于下限。' });
      return;
    }
    if (values.horizonDays === null || values.horizonDays < 0 || values.horizonDays > 365) {
      setAnalysis({ status: 'error', result: null, error: '持有上限天数需在 0–365 天内。' });
      return;
    }
    if (values.target === null || values.target <= 0 || values.profitTarget === null || values.profitTarget < 0) {
      setAnalysis({ status: 'error', result: null, error: '请填写有效的目标点位和不小于零的盈利目标。' });
      return;
    }
    if (values.ivShift === null || values.ivShift < -10 || values.ivShift > 10) {
      setAnalysis({ status: 'error', result: null, error: 'IV 平移需在 -10 到 +10 pp 内。' });
      return;
    }
    if (values.feeRoundtrip === null || values.feeRoundtrip < 0 || values.feeRoundtrip > 100000) {
      setAnalysis({ status: 'error', result: null, error: '请填写 0–100000 元之间的往返成本估算。' });
      return;
    }
    const controller = new AbortController();
    analysisControllerRef.current = controller;
    setAnalysis({ status: 'loading', result: null, error: '' });
    const body = {
      snapshot_id: activeSnapshot.snapshot_id,
      legs: selectedLegs.map((leg) => ({ code: leg.code, side: leg.side, quantity: leg.quantity })),
      price_min: values.priceMin,
      price_max: values.priceMax,
      price_steps: 15,
      horizon_days: values.horizonDays,
      time_steps: 8,
      iv_shift_pp: values.ivShift,
      target: values.target,
      profit_target: values.profitTarget,
      fee_roundtrip: values.feeRoundtrip,
      slippage_points: 0,
      stress_spread_multiplier: 2,
    };
    if (historyKey) body.history_key = historyKey;
    try {
      const response = await fetch(EXIT_ENDPOINT, {
        method: 'POST',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(await responseError(response, '退出分析请求失败'));
      const result = await response.json();
      if (!result || !Array.isArray(result.prices) || !Array.isArray(result.days) || !Array.isArray(result.cells)) throw new Error('退出分析响应格式不完整');
      if (requestId !== analysisRequestRef.current) return;
      setAnalysis({ status: 'ready', result, error: '' });
    } catch (error) {
      if (requestId !== analysisRequestRef.current || error?.name === 'AbortError') return;
      setAnalysis({ status: 'error', result: null, error: error?.message || '退出分析失败' });
    } finally {
      if (requestId === analysisRequestRef.current && analysisControllerRef.current === controller) analysisControllerRef.current = null;
    }
  };

  const analysisSnapshotReady = Boolean(activeSnapshot?.snapshot_id) && (!historyKey || historyLoad.status === 'ready');
  const historyItemLabel = (item) => `${item.trade_date || formatDate(item.asof)} · ${formatDate(item.asof)}`;
  const historical = Boolean(historyKey);
  const result = analysis.result;
  const resultAsOf = result?.asof || activeSnapshot?.asof;

  return (
    <section className="exit-lab" aria-labelledby="exit-lab-title">
      <div className="exit-lab-heading">
        <div>
          <h2 id="exit-lab-title">退出分析</h2>
          <p>按目标指数与持有天数扫描提前退出损益，结果绑定一个行情截面。</p>
        </div>
        <div className="exit-lab-heading-actions">
          <span className={`exit-lab-basis-tag ${historical ? 'exit-lab-basis-history' : 'exit-lab-basis-current'}`}>{historical ? '历史基准' : activeSnapshot?.mode === 'live' ? '手动截面' : '历史基准'}</span>
          <button className="options-primary-button" disabled={analysis.status === 'loading' || historyLoad.status === 'loading' || !analysisSnapshotReady} onClick={runAnalysis} type="button">
            {analysis.status === 'loading' ? <><span className="options-spinner" />分析中…</> : '更新分析'}
          </button>
        </div>
      </div>

      <div className="exit-lab-history-row">
        <label>历史快照
          <select aria-label="选择历史快照" onChange={chooseHistory} value={historyKey}>
            <option value="">当前截面 · {formatDate(normalizedCurrentSnapshot?.asof)}</option>
            {history.items.filter((item) => item.key !== history.currentKey).map((item) => <option key={item.key} value={item.key}>{historyItemLabel(item)}</option>)}
          </select>
        </label>
        <span className="exit-lab-history-status">
          {history.status === 'loading' ? '历史列表读取中…' : history.error ? history.error : history.items.length ? `可回看 ${history.items.length} 个保存截面` : '暂无保存截面'}
        </span>
        {historyLoad.status === 'loading' ? <span className="exit-lab-history-status">正在读取所选历史快照…</span> : null}
        {historyLoad.error ? <span className="exit-lab-inline-error">{historyLoad.error}</span> : null}
      </div>

      {historical && activeSnapshot ? <div className="exit-lab-history-banner">历史局部回看：<strong>{formatDate(activeSnapshot.asof)}</strong> · 交易日 {formatDate(activeSnapshot.trade_date)}；不会改变全局行情或比较篮。</div> : null}
      {!historical && activeSnapshot ? <div className="exit-lab-current-banner">分析基准：<strong>{formatDate(activeSnapshot.asof)}</strong> · 交易日 {formatDate(activeSnapshot.trade_date)} · 当前行情截面</div> : null}

      <div className="exit-lab-controls-grid">
        <section className="options-surface exit-lab-control-surface">
          <div className="exit-lab-subheading"><div><h3>分析组合</h3><p>空篮时自动生成三种比较候选；有篮时保留当前比较篮选项。</p></div></div>
          <label className="exit-lab-field exit-lab-portfolio-field">选择组合
            <select aria-label="选择退出分析组合" disabled={!portfolioOptions.length} onChange={choosePortfolio} value={selectedPortfolio?.id || ''}>
              {!portfolioOptions.length ? <option value="">暂无可用候选</option> : null}
              {portfolioOptions.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          </label>
          {selectedPortfolio?.description ? <p className="exit-lab-description">{selectedPortfolio.description}</p> : null}
          <div className="exit-lab-selected-legs">
            <span className="exit-lab-label">本次分析腿</span>
            <PortfolioLegs legs={selectedLegs} monthId={activeMonth?.id} emptyText={activeSnapshot ? '当前快照没有满足条件的候选组合。' : '等待分析快照。'} />
          </div>
          <div className="exit-lab-current-basket">
            <span className="exit-lab-label">当前比较篮（全局）</span>
            <PortfolioLegs legs={currentBasket} monthId={month?.id} emptyText="当前比较篮为空；将使用上方候选组合。" />
          </div>
        </section>

        <section className="options-surface exit-lab-control-surface">
          <div className="exit-lab-subheading"><div><h3>退出参数</h3><p>数字均为本页局部配置，改变后旧结果立即清空。</p></div></div>
          <div className="exit-lab-form-grid">
            <label className="exit-lab-field">目标点位
              <input inputMode="decimal" min="0" onChange={updateField(setTargetInput)} step="1" type="number" value={targetInput} />
            </label>
            <label className="exit-lab-field">盈利目标（元）
              <input inputMode="decimal" min="0" onChange={updateField(setProfitTarget)} step="100" type="number" value={profitTarget} />
            </label>
            <label className="exit-lab-field">持有上限（天）
              <input inputMode="decimal" max="365" min="0" onChange={updateField(setHorizonDays)} step="0.5" type="number" value={horizonDays} />
            </label>
            <label className="exit-lab-field">价格下限
              <input inputMode="decimal" min="0" onChange={updateField(setPriceMin)} step="0.1" type="number" value={priceMin} />
            </label>
            <label className="exit-lab-field">价格上限
              <input inputMode="decimal" min="0" onChange={updateField(setPriceMax)} step="0.1" type="number" value={priceMax} />
            </label>
            <label className="exit-lab-field">IV 平移
              <span className="exit-lab-range-line"><input aria-label="IV 平移" max="10" min="-10" onChange={updateField(setIvShift)} step="0.5" type="range" value={ivShift} /><output>{toNumber(ivShift) === null ? MISSING : `${Number(ivShift) > 0 ? '+' : ''}${ivShift} pp`}</output></span>
            </label>
          </div>
          <p className="exit-lab-assumption-note">比较价格、时间与 IV 对损益的影响；保守模式用于观察退出价差扩大的影响。情景不代表触达概率。</p>
        </section>
      </div>

      <details className="options-surface exit-lab-costs">
        <summary>交易成本（可选）<span>{toNumber(feeRoundtrip) === 0 ? '未计额外成本' : `每腿每手往返约 ${formatNumber(feeRoundtrip)} 元`}</span></summary>
        <label className="exit-lab-field">往返成本估算（元/手，每腿）
          <input inputMode="decimal" min="0" max="100000" onChange={updateField(setFeeRoundtrip)} step="5" type="number" value={feeRoundtrip} />
        </label>
        <p className="exit-lab-assumption-note">填手续费与额外滑点的合计估算即可，按各腿手数计入损益。普通买卖价差已包含在报价中，不必重复估算；保守模式固定将退出半价差放大至 2 倍。</p>
      </details>

      {analysis.error ? <div className="exit-lab-error" role="alert">{analysis.error}</div> : null}
      {!activeSnapshot && !historical ? <div className="options-surface exit-lab-empty-state">等待当前期权快照后开始退出分析。</div> : null}
      {historical && !activeSnapshot ? <div className="options-surface exit-lab-empty-state">正在读取历史快照，读取完成后可更新分析。</div> : null}

      {result ? (
        <section className="options-surface exit-lab-result-surface">
          <div className="exit-lab-result-heading">
            <div><h3>退出损益结果</h3><p>结果基于所选行情截面 · 基准时点 {formatDate(resultAsOf)}</p></div>
            <div className="options-segment" aria-label="热图退出价模式">
              <button className={displayMode === 'normal' ? 'options-segment-on' : ''} onClick={() => setDisplayMode('normal')} type="button">普通退出价</button>
              <button className={displayMode === 'stress' ? 'options-segment-on' : ''} onClick={() => setDisplayMode('stress')} type="button">保守退出价</button>
            </div>
          </div>
          <EntrySummary entry={result.entry} />
          <div className="exit-lab-chart-head"><span><i className="exit-lab-color-dot exit-lab-dot-profit" />盈利</span><span><i className="exit-lab-color-dot exit-lab-dot-neutral" />零/中性</span><span><i className="exit-lab-color-dot exit-lab-dot-loss" />亏损</span><small>当前显示：{displayMode === 'stress' ? '保守退出价' : '普通退出价'}；悬停可同时查看两种损益</small></div>
          <Heatmap asof={resultAsOf} displayMode={displayMode} result={result} />
          <TargetTable asof={resultAsOf} result={result} />
          <ReverseTable asof={resultAsOf} result={result} />
          {Array.isArray(result.warnings) && result.warnings.length ? <div className="exit-lab-warnings"><span>计算说明</span>{result.warnings.map((warning, index) => <span key={`${warning}-${index}`}>{warning}</span>)}</div> : null}
        </section>
      ) : null}
    </section>
  );
}
