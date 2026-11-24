import { useMemo, useRef } from 'react';
import { useChart, usePreference, axisTooltip } from './charts.js';
import { surfaceMetrics } from './surfaceMetrics.js';
import { OPTION_IV_CURVE } from './holdings.js';

export const MONTH_COLORS = ['#4e7fb9', '#8c7b9f', '#568f84', '#a48b59', '#505c6b', '#a17589'];

export function ColoredCode({ code, color }) {
  const text = String(code || '—');
  const match = text.match(/^([A-Za-z]+\d{2})(\d{2})(.*)$/);
  return match ? <>{match[1]}<span style={{color}}>{match[2]}</span>{match[3]}</> : text;
}
const fmt = (x, digits = 1) => Number.isFinite(x) ? x.toFixed(digits) : '—';
const change = (current, previous) => Number.isFinite(current) && Number.isFinite(previous) ? current - previous : null;
const signed = value => Number.isFinite(value) ? (value >= 0.05 ? '+' : '') + fmt(Math.abs(value) < 0.05 ? 0 : value) : '—';

export function TermOverview({ surface, previous, date, onSelect }) {
  const chartRef = useRef(null);
  const [mode, setMode] = usePreference('options.term.mode', 'levels');
  const data = useMemo(() => {
    const prior = new Map(surfaceMetrics(previous?.rows, previous?.months).map(row => [row.id, row]));
    return surface.map(row => {
      const before = prior.get(row.id);
      const iv = row.atm_iv === null ? null : row.atm_iv * 100;
      const previousIv = Number.isFinite(before?.atm_iv) ? before.atm_iv * 100 : null;
      return { ...row, iv, previousIv, ivChange: change(iv, previousIv), skewChange: change(row.skew25_pp, before?.skew25_pp) };
    });
  }, [surface, previous]);
  const diff = mode === 'change' && !!previous;
  useChart(chartRef, chart => {
    const line = (name, key, dashed = false) => ({
      name, type: 'line', symbolSize: 5, connectNulls: false,
      lineStyle: { width: 2, type: dashed ? 'dashed' : 'solid' }, data: data.map(row => row[key]),
    });
    chart.setOption({
      animation: false, color: ['#3d70ad', '#8d9a93'],
      grid: { left: 48, right: 20, top: 34, bottom: 34 },
      legend: { top: 0, left: 'center', itemGap: 28, itemWidth: 14, itemHeight: 8, textStyle: { color: '#505c6b', fontSize: 12 }, show: !diff },
      tooltip: { trigger: 'axis', confine: true, renderMode: 'richText',
        formatter: items => axisTooltip(items, value => diff ? signed(value) + ' pp' : fmt(value) + '%') },
      xAxis: { type: 'category', data: data.map(row => row.code), splitLine: {show:false}, axisLabel: { interval: 0, fontSize: 11, formatter: code => code.slice(-4) }, axisLine: { onZero: false } },
      yAxis: { type: 'value', scale: !diff, axisLabel: { formatter: diff ? '{value} pp' : '{value}%' },
        axisLine: {show:false}, axisTick: {show:false}, splitLine: {show:false} },
      series: diff ? [{ name: '平值 IV 日变动', type: 'bar', barMaxWidth: 32, data: data.map(row => ({
        value: row.ivChange, itemStyle: { color: row.ivChange >= 0 ? '#b64d49' : '#2b8167' },
      })) }] : [line(date, 'iv'), ...(previous ? [line(previous.trade_date, 'previousIv', true)] : [])],
    }, true);
    chart.off('click');
    chart.on('click', 'series', event => {
      if (data[event.dataIndex]) onSelect?.(data[event.dataIndex].id);
    });
  }, [data, date, diff, previous, onSelect]);
  return <section className="options-surface options-term">
    <div className="options-heading">
      <h2>隐波期限结构</h2>
      {previous && <button className="options-plain-button options-term-toggle" type="button"
        aria-pressed={diff} onClick={() => setMode(diff ? 'levels' : 'change')}>日变动</button>}
    </div>
    <div className="options-term-grid">
      {data.some(row => Number.isFinite(diff ? row.ivChange : row.iv)) ?
        <div ref={chartRef} className="options-term-chart" role="img" aria-label={diff ? '各月平值隐波日变动' : '平值隐波期限对照'}/> :
        <p className="options-note">暂无有效{diff ? '日变动' : '平值隐波'}</p>}
      <table className="options-mini-table options-term-table">
        <thead><tr><th scope="col">到期月</th><th scope="col">平值 IV</th><th scope="col">日变动</th><th scope="col">25Δ 偏度</th><th scope="col">日变动</th></tr></thead>
        <tbody>{data.map(row => <tr key={row.id}>
          <th scope="row"><button type="button" onClick={() => onSelect?.(row.id)}>{row.code}</button></th>
          <td>{Number.isFinite(row.iv) ? fmt(row.iv) + '%' : '—'}</td>
          <td className={row.ivChange > 0 ? 'options-up' : row.ivChange < 0 ? 'options-down' : undefined}>{signed(row.ivChange)}</td>
          <td>{signed(row.skew25_pp)}</td>
          <td className={row.skewChange > 0 ? 'options-up' : row.skewChange < 0 ? 'options-down' : undefined}>{signed(row.skewChange)}</td>
        </tr>)}</tbody>
      </table>
    </div>
  </section>;
}

export default function SurfaceComparison({ data, metric }) {
  const chartRef = useRef(null);
  const [hidden, setHidden] = usePreference('options.surface.hidden.' + metric, []);
  const months = useMemo(() => data.map((month, i) => ({ ...month, color: MONTH_COLORS[i % MONTH_COLORS.length] })), [data]);
  const visible = months.filter(month => !hidden.includes(month.id));
  useChart(chartRef, (chart) => {
    const series = metric === 'smiles' ? visible.map(m => ({
      name: m.code, type: 'line', ...OPTION_IV_CURVE, showSymbol: true, symbolSize: 3,
      data: m.points.map(p => [p.x, p.iv === null ? null : p.iv * 100]),
      color: m.color,
    })) : [{name: metric === 'term' ? 'ATM IV' : '25Δ偏度', type: metric === 'term' ? 'line' : 'bar', connectNulls: false,
      data: visible.map(m => metric === 'term' ? (m.atm_iv === null ? null : m.atm_iv * 100) : ({value:m.skew25_pp, itemStyle:{color:m.skew25_pp >= 0 ? '#b64d49' : '#2b8167'}})), color:'#4e7fb9'}];
    chart.setOption({ animation:false, grid:{left:55,right:25,top:40,bottom:45}, legend:metric === 'smiles' ? {top:0} : undefined,
      tooltip:{trigger:'axis',renderMode:'richText',formatter:p=>axisTooltip(p,v=>`${fmt(Number(v), 1)}${metric === 'skew' ? ' pp' : '%'}`, metric === 'smiles' ? `K/F ${fmt(Number(p[0]?.axisValue), 2)}` : undefined)},
      xAxis:metric === 'smiles' ? {type:'value',scale:true,min:'dataMin',max:'dataMax',splitLine:{show:false},name:'K / F',nameLocation:'middle',nameGap:28,axisLabel:{formatter:x=>Number(x).toFixed(2)}} : {type:'category',splitLine:{show:false},data:visible.map(m=>m.code)},
      yAxis:{type:'value',scale:true,name:metric === 'skew' ? 'Put IV − Call IV (pp)' : 'IV (%)',axisLine:{show:false},axisTick:{show:false},splitLine:{show:false}}, series }, true);
  }, [data, hidden, metric]);
  return <section className="options-surface">
    <div className="options-analysis-tools">
      {months.map(month => {
        const pressed = !hidden.includes(month.id);
        return <button key={month.id} type="button" className="options-plain-button" aria-pressed={pressed}
          style={{borderColor: pressed ? month.color : '#ddd', opacity: pressed ? 1 : .5}}
          onClick={() => setHidden(current => current.includes(month.id) ? current.filter(id => id !== month.id) : [...current, month.id])}>
          <ColoredCode code={month.code} color={month.color} />
        </button>;
      })}
    </div>
    <div ref={chartRef} className="options-vol-chart" role="img" aria-label="跨月波动率比较图" />
    {visible.length > 0 && visible.every(month => month.forward === null) && <p className="options-note">缺少有效远期</p>}
    <div className="options-mini-table-wrap" tabIndex={0} role="region" aria-label="跨月波动率比较表，可横向滚动">
      <table className="options-mini-table">
        <thead><tr>
          <th>月份</th><th>截面剩余日历 / 交易日</th><th>隐含远期 F</th><th>ATM IV</th>
          <th>Call 25Δ IV</th><th>Put 25Δ IV</th><th>25Δ偏度 pp</th>
        </tr></thead>
        <tbody>{visible.map(month => <tr key={month.id}>
          <td><ColoredCode code={month.code} color={month.color} /></td>
          <td>{month.calendar_days ?? '—'} / {month.trading_days ?? '—'}</td>
          <td>{fmt(month.forward)}</td>
          {[month.atm_iv, month.call25_iv, month.put25_iv].map((value, i) =>
            <td key={i}>{Number.isFinite(value) ? fmt(value * 100) + '%' : '—'}</td>)}
          <td>{fmt(month.skew25_pp, 2)}</td>
        </tr>)}</tbody>
      </table>
    </div>
  </section>;
}
