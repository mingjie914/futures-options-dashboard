import { useEffect, useMemo, useRef, useState } from 'react';
import * as echarts from 'echarts';
import { surfaceMetrics } from './surfaceMetrics.js';

const colors = ['#3274d2', '#8b5dc5', '#38878c', '#af8033', '#6f7ec0', '#a66587'];
const fmt = (x, digits = 1) => Number.isFinite(x) ? x.toFixed(digits) : '—';
export default function SurfaceComparison({ rows, months, metric }) {
  const chartRef = useRef(null);
  const data = useMemo(() => surfaceMetrics(rows, months), [rows, months]);
  const [hidden, setHidden] = useState([]);
  const visible = data.filter(m => !hidden.includes(m.id));
  useEffect(() => {
    if (!chartRef.current) return;
    const chart = echarts.init(chartRef.current);
    const series = metric === 'smiles' ? visible.map(m => ({
      name: m.code, type: 'line', connectNulls: false, showSymbol: true, symbolSize: 4,
      data: m.points.map(p => [p.x, p.iv === null ? null : p.iv * 100]),
      color: colors[data.findIndex(item => item.id === m.id) % colors.length],
    })) : [{name: metric === 'term' ? 'ATM IV' : '25Δ偏度', type: metric === 'term' ? 'line' : 'bar', connectNulls: false,
      data: visible.map(m => metric === 'term' ? (m.atm_iv === null ? null : m.atm_iv * 100) : ({value:m.skew25_pp, itemStyle:{color:m.skew25_pp >= 0 ? '#cf4b50' : '#29876d'}})), color:'#3274d2'}];
    chart.setOption({ animation:false, grid:{left:55,right:25,top:40,bottom:45}, legend:metric === 'smiles' ? {top:0} : undefined,
      tooltip:{trigger:'axis',valueFormatter:x => x == null ? '—' : `${Number(x).toFixed(2)}${metric === 'skew' ? ' pp' : '%'}`},
      xAxis:metric === 'smiles' ? {type:'value',scale:true,min:'dataMin',max:'dataMax',name:'K / F',nameLocation:'middle',nameGap:28,axisLabel:{formatter:x=>Number(x).toFixed(2)}} : {type:'category',data:visible.map(m=>m.code)},
      yAxis:{type:'value',scale:true,name:metric === 'skew' ? 'Put IV − Call IV (pp)' : 'IV (%)',splitLine:{lineStyle:{color:'#edf0f5'}}}, series });
    const observer = new ResizeObserver(() => chart.resize()); observer.observe(chartRef.current);
    return () => {observer.disconnect();chart.dispose();};
  }, [data, hidden, metric]);
  return <section className="options-surface">
    <div className="options-analysis-tools">{data.map((m,i)=><button key={m.id} type="button" className="options-plain-button" aria-pressed={!hidden.includes(m.id)} style={{borderColor:hidden.includes(m.id)?'#ddd':colors[i%colors.length],opacity:hidden.includes(m.id)?.5:1}} onClick={()=>setHidden(current=>current.includes(m.id)?current.filter(id=>id!==m.id):[...current,m.id])}>{m.code.slice(0,-2)}<strong style={{color:colors[i%colors.length]}}>{m.code.slice(-2)}</strong></button>)}</div>
    <div ref={chartRef} className="options-vol-chart" aria-label="跨月波动率比较图" />
    {visible.some(m => m.forward === null) ? <p className="options-time-context">{visible.filter(m => m.forward === null).map(m => m.code).join('、')} 缺少可用隐含远期，IV 指标保留为空；有买卖报价不等于可计算 IV。</p> : null}
    <p className="options-note">横向比较统一采用隐含远期 F；微笑横轴 K/F，K&lt;F 取 Put，K≥F 取 Call。仅用价差率≤20%的报价；缺口不连线、不外推。ATM 在 K/F=1 插值，25Δ 使用贴现远期 Delta；偏度 = Put 25Δ IV − Call 25Δ IV。</p>
    <div className="options-mini-table-wrap"><table className="options-mini-table"><thead><tr><th>月份</th><th>截面剩余日历 / 交易日</th><th>隐含远期 F</th><th>ATM IV</th><th>Call 25Δ IV</th><th>Put 25Δ IV</th><th>25Δ偏度 pp</th></tr></thead><tbody>{visible.map((m,i)=><tr key={m.id}><td>{m.code.slice(0,-2)}<strong style={{color:colors[data.findIndex(x=>x.id===m.id)%colors.length]}}>{m.code.slice(-2)}</strong></td><td>{m.calendar_days ?? '—'} / {m.trading_days ?? '—'}</td><td>{fmt(m.forward)}</td><td>{m.atm_iv === null ? '—' : `${fmt(m.atm_iv*100)}%`}</td><td>{m.call25_iv === null ? '—' : `${fmt(m.call25_iv*100)}%`}</td><td>{m.put25_iv === null ? '—' : `${fmt(m.put25_iv*100)}%`}</td><td>{fmt(m.skew25_pp,2)}</td></tr>)}</tbody></table></div>
    <p className="options-note">期限结构采用 ACT/365F，节假日分布不同，跨月 IV 高低不直接等于套利机会。历史样本尚不足，暂不展示 IV 历史百分位。</p>
  </section>;
}
