import {useEffect, useRef, useState} from 'react';
import * as ec from 'echarts/core';
import {LineChart, HeatmapChart} from 'echarts/charts';
import {GridComponent, TooltipComponent, DataZoomComponent, LegendComponent, VisualMapComponent} from 'echarts/components';
import {CanvasRenderer} from 'echarts/renderers';
import {lineSegments, tooltipRows, seasonalSelection} from './lineSegments.mjs';
ec.use([LineChart, HeatmapChart, GridComponent, TooltipComponent, DataZoomComponent, LegendComponent, VisualMapComponent, CanvasRenderer]);

export function HistoryChart({result, heatmap=false, showAllYears=false}) {
  const ref = useRef();
  useEffect(() => {
    const chart = ec.init(ref.current,{textStyle:{fontFamily:getComputedStyle(ref.current).fontFamily},tooltip:{textStyle:{fontFamily:getComputedStyle(ref.current).fontFamily}}});
    const share = result.metric === 'position_share' && result.mode !== 'spread';
    const integer = !share && result.metric !== 'price' && result.mode !== 'spread';
    const display = value => value == null ? '—' : Number(value * (share ? 100 : 1)).toLocaleString('zh-CN', {maximumFractionDigits: integer ? 0 : 2}) + (share ? '%' : '');
    const byLine = result.lines.map(line => new Map(line.points.map(point => [point.x, point])));
    const segments = heatmap ? [] : lineSegments(result.axis, result.lines);
    let selected = result.mode === 'seasonal' ? seasonalSelection(result.lines, showAllYears) : {};
    const palette = ['#2563eb', '#0d9488', '#d97706', '#9333ea', '#e11d48', '#64748b', '#0891b2', '#65a30d', '#c026d3', '#b45309'];
    const axisIndex = new Map(result.axis.map((x, i) => [x, i]));
    const cells = [];
    if (heatmap) result.lines.forEach((line, y) => line.points.forEach(p => {
      if (p.value != null) cells.push([axisIndex.get(p.x), y, p.value]);
    }));
    const common = {
      animation: false,
      grid: {left: 90, right: 30, top: 35, bottom: heatmap ? 115 : 85},
      xAxis: {type: 'category', data: result.axis, boundaryGap: heatmap, axisLabel: {hideOverlap: true}},
      dataZoom: [{type: 'inside', xAxisIndex: 0}, {type: 'slider', xAxisIndex: 0, bottom: heatmap ? 65 : 35, height: 20}],
    };
    const option = heatmap ? {
      ...common,
      tooltip: {renderMode: 'richText', formatter: p => `${result.axis[p.value[0]]}\n${result.lines[p.value[1]].label}\n${display(p.value[2])} ${result.unit}`},
      yAxis: {type: 'category', data: result.lines.map(l => l.label), axisLabel: {fontSize: 10}},
      visualMap: {min: 0, max: cells.reduce((max, p) => Math.max(max, p[2]), 1), calculable: true, orient: 'horizontal', left: 'center', bottom: 0, inRange: {color: ['#eff6ff', '#93c5fd', '#2563eb', '#172554']}, formatter: display},
      series: [{type: 'heatmap', data: cells, progressive: 4000, emphasis: {itemStyle: {borderColor: '#d97706', borderWidth: 1}}}],
    } : {
      ...common,
      color: ['#2563eb', '#0d9488', '#d97706', '#9333ea', '#e11d48', '#64748b', '#0891b2', '#65a30d', '#c026d3', '#b45309'],
      legend: {type: 'scroll', bottom: 0, data: result.lines.map(l => l.label), selected,
        scrollDataIndex: result.mode === 'seasonal' && !showAllYears ? Math.max(0,result.lines.length-5) : 0},
      tooltip: {trigger: 'axis', confine: true, renderMode: 'richText', formatter: params => {
        const list = Array.isArray(params) ? params : [params];
        const rows = tooltipRows(params, result.axis, segments, byLine);
        const heading = list[0]?.axisValue ?? result.axis[list[0]?.dataIndex] ?? '';
        const present = new Set(rows.map(r => r.lineIndex));
        const absent = result.mode === 'overview' ? [] : result.lines.filter((line, i) => !present.has(i) && selected[line.label] !== false);
        return [heading, ...rows.map(({name, marker, point}) =>
          `${marker} ${name} · ${point.trade_date}: ${display(point.value)}${share ? '' : ' '+(result.unit || '手')}` +
          (result.mode === 'spread' ? `（近 ${display(point.near_price)} / 远 ${display(point.far_price)}）` : '')),
          ...absent.map(line => `${line.label}: —（该对齐日无观测）`),
          ...(rows.length ? [] : ['该日无有效观测（连线不代表补值）'])].join('\n');
      }},
      yAxis: {type: 'value', scale: true, axisLabel: {formatter: display}, splitLine: {lineStyle: {color: '#edf1f6'}}},
      series: segments.map(({name, lineIndex, data}) => ({name, type: 'line', showSymbol: result.mode !== 'overview', symbolSize: 3, connectNulls: true, emphasis: {focus: 'series'},
        itemStyle: {color: palette[lineIndex % palette.length]},
        lineStyle: {color: palette[lineIndex % palette.length], width: lineIndex === result.lines.length-1 ? 2.8 : 1.3}, data})),
    };
    chart.setOption(option);
    chart.on('legendselectchanged', event => {selected = event.selected;});
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(ref.current);
    return () => {observer.disconnect(); chart.dispose();};
  }, [result, heatmap, showAllYears]);
  return <div ref={ref} className="history-chart" style={{height: heatmap ? Math.min(850, Math.max(420, result.lines.length * 12)) : 440}} role="img" aria-label={result.accessible_label || (heatmap ? '全合约持仓热力图' : '历年合约对比图')}/>;
}

export default function HistoryPanel({product, end, near, far, priceType, revision}) {
  const [mode, setMode] = useState('same_month');
  const [years, setYears] = useState(5);
  const [month, setMonth] = useState(Number(near?.slice(-2)) || 10);
  const [farMonth, setFarMonth] = useState(Number(far?.slice(-2)) || 1);
  const [offset, setOffset] = useState(near && far && near.slice(-4,-2) === far.slice(-4,-2) ? 0 : 1);
  const [metric, setMetric] = useState('position');
  const [overviewMetric, setOverviewMetric] = useState('price');
  const [overviewStyle, setOverviewStyle] = useState('line');
  const [result, setResult] = useState(null), [error, setError] = useState('');
  const anchorYear = 2000 + Number(near?.slice(-4,-2) || end.slice(2,4));
  const effectiveMetric = mode === 'spread' ? 'price' : mode === 'overview' ? overviewMetric : metric;
  const summary = result?.seasonal_summary;
  const fmt = value => value == null ? '—' : Number(value).toLocaleString('zh-CN', {maximumFractionDigits: 2});
  useEffect(() => {
    const controller = new AbortController();
    setResult(null); setError('');
    const query = new URLSearchParams({product, end, mode, years, near_month: month, far_month: farMonth, far_year_offset: offset, metric: effectiveMetric, price_type: priceType, anchor_year: anchorYear});
    fetch('/api/contract-history?' + query, {signal: controller.signal}).then(async response => {
      const data = await response.json();
      if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail));
      setResult(data);
    }).catch(e => {if (e.name !== 'AbortError') setError(e.message);});
    return () => controller.abort();
  }, [product, end, mode, years, month, farMonth, offset, effectiveMetric, priceType, anchorYear, revision]);
  return <section className="panel history-panel">
    <div className="history-tabs">{[['same_month','同交割月份对比'],['spread','固定月份价差'],['overview','全合约总览']].map(([key, title]) => <button key={key} className={mode === key ? 'primary' : ''} onClick={() => setMode(key)}>{title}</button>)}</div>
    <div className="filters">
      <label>历史跨度<select value={years} onChange={e => setYears(Number(e.target.value))}>{[3,5,10].map(n => <option key={n} value={n}>{n}年</option>)}</select></label>
      {mode !== 'overview' && <label>{mode === 'spread' ? '近月交割月份' : '交割月份'}<select value={month} onChange={e => setMonth(Number(e.target.value))}>{Array.from({length:12}, (_,i) => <option key={i+1} value={i+1}>{i+1}月</option>)}</select></label>}
      {mode === 'spread' && <><label>远月交割月份<select value={farMonth} onChange={e => setFarMonth(Number(e.target.value))}>{Array.from({length:12}, (_,i) => <option key={i+1} value={i+1}>{i+1}月</option>)}</select></label><label>远月年份<select value={offset} onChange={e => setOffset(Number(e.target.value))}><option value={0}>同年</option><option value={1}>次年</option></select></label></>}
      {mode === 'same_month' && <label>对比指标<select value={metric} onChange={e => setMetric(e.target.value)}><option value="position">持仓手数</option><option value="position_share">占品种总持仓比例</option><option value="price">合约价格</option><option value="volume">成交量</option></select></label>}
      {mode === 'overview' && <><label>对比指标<select value={overviewMetric} onChange={e => setOverviewMetric(e.target.value)}><option value="price">合约价格</option><option value="position">持仓手数</option><option value="position_share">占品种总持仓比例</option></select></label>{overviewMetric !== 'price' && <label>呈现方式<select value={overviewStyle} onChange={e => setOverviewStyle(e.target.value)}><option value="line">各合约曲线</option><option value="heatmap">持仓热力图</option></select></label>}</>}
    </div>
    <p className="sub">{mode === 'overview' ? '各真实合约按交易日期平铺，不拼接主力、不合计成一条总持仓。曲线悬停列出同日有效合约；热力图横轴日期、纵轴合约。' : `交割年份 ${anchorYear-years+1}—${anchorYear}；Y-1 = 交割前一年，Y+0 = 交割当年。`}
      {mode === 'spread' ? '价差 = 近月 − 远月；仅同日双腿有效报价参与。' : ''} {effectiveMetric === 'price' ? (priceType === 'close' ? '收盘' : '结算')+'价格口径。' : ''}历史窗口独立于顶部开始日期。</p>
    {error ? <p className="error" role="alert">{error}</p> : !result ? <p className="empty" role="status">正在读取历史合约…</p> : <>
      <p className="sub">数据截止 {result.meta.as_of || '无'} · {result.lines.length}条序列 · 源读取始于 {result.meta.read_start} {result.meta.stale ? ' · ⚠ 缓存降级' : ''} · 缺失点不插值，点击图例可隐藏序列。</p>
      {summary && <section aria-label="同季节价差基准">
        <h3>同季节基准 · 不含当前交割年份</h3>
        {summary.current ? <><p>当前 {summary.current.label}：{fmt(summary.current.value)} · 观测日 {summary.current.trade_date}{summary.current.lag_days > 0 ? `（距截止日 ${summary.current.lag_days} 天，非截止日新报价）` : ''}</p>
          <p>历史有效年度 {summary.n} · 中位数 {fmt(summary.median)} · 范围 {fmt(summary.min)} ～ {fmt(summary.max)} · 当前减中位数 {summary.median == null ? '—' : fmt(summary.current.value-summary.median)}（原始报价单位）</p>
          <details><summary>查看逐年同期观测与缺失说明</summary><div className="table-wrap"><table><thead><tr><th>合约组合</th><th>同期目标日</th><th>实际观测日</th><th className="num">价差</th></tr></thead><tbody>{summary.samples.map(p => <tr key={p.year}><td>{p.label}</td><td>{p.target_date}</td><td>{p.trade_date}</td><td className="num">{fmt(p.value)}</td></tr>)}</tbody></table></div>{summary.excluded.map(p => <p key={p.year} className="sub">{p.year}：{{missing_cohort:'缺少该年组合',invalid_calendar_date:'该年不存在对应月日',no_observation_within_7_days:'目标日向前7天内无有效观测'}[p.reason] || p.reason}</p>)}</details>
        </> : <p className="empty">当前交割年份无有效观测，不能计算同期基准。</p>}
        <p className="sub">{years}年跨度包含当前交割年份；每个历史年度最多一个样本，以当前实际观测月日向前最多7天匹配。不是胜率、置信区间或交易信号。</p>
      </section>}
      {result.lines.length ? <HistoryChart result={result} heatmap={mode === 'overview' && effectiveMetric !== 'price' && overviewStyle === 'heatmap'}/> : <p className="empty">所选月份组合在当前数据中没有可比较序列</p>}
      {result.missing.length > 0 && <p className="sub">未观测到合约或缺少一腿：{result.missing.join('、')}</p>}
      <details><summary>口径与数据覆盖</summary><ul>{result.warnings.map(w => <li key={w}>{w}</li>)}</ul><div className="table-wrap"><table><thead><tr><th>序列</th><th>首个有效日期</th><th>最后有效日期</th><th className="num">有效观测数</th></tr></thead><tbody>{result.lines.map(line => {const valid = line.points.filter(p => p.value != null); return <tr key={line.label}><td>{line.label}</td><td>{valid[0]?.trade_date || '—'}</td><td>{valid.at(-1)?.trade_date || '—'}</td><td className="num">{valid.length}</td></tr>;})}</tbody></table></div></details>
    </>}
  </section>;
}
