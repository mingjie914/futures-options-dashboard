import { conditionalFetch as fetch } from './transport.js';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useChart, ChartFrame, Metric, usePreference } from './charts.js';
import { surfaceMetrics } from './surfaceMetrics.js';

const colors = ['#4e7fb9', '#8c7b9f', '#568f84', '#a48b59', '#505c6b', '#a17589'];
const fmt = (x, digits = 1) => Number.isFinite(x) ? x.toFixed(digits) : '—';

export function IvHistory({rows,spot,snapshot,monthId}) {
  const [code,setCode]=usePreference('options.iv.'+monthId+'.code',''),[window,setWindow]=usePreference('options.iv.window',60),[data,setData]=useState(null),[error,setError]=useState('');
  const ref=useRef();
  const ordered=useMemo(()=>[...rows].sort((a,b)=>a.strike-b.strike||a.cp.localeCompare(b.cp)),[rows]);
  const selected=ordered.find(r=>r.code===code)||ordered.reduce((best,row)=>!best||Math.abs(row.strike-spot)<Math.abs(best.strike-spot)?row:best,null);
  const id=selected?.code;
  const result=data?.code===id&&data?.window===window?data:null;
  useEffect(()=>{const c=new AbortController();setData(null);setError('');if(!id)return()=>c.abort();
    fetch('/api/options/v1/iv-history?'+new URLSearchParams({code:id,window,snapshot_id:snapshot.snapshot_id}),{signal:c.signal})
      .then(async r=>{const d=await r.json();if(!r.ok)throw Error(d.detail||'历史读取失败');return {...d,window};})
      .then(setData).catch(e=>{if(e.name!=='AbortError')setError(e.message);});return()=>c.abort();
  },[id,window,snapshot.snapshot_id]);
  useChart(ref,chart=>chart.setOption({animation:false,color:['#4e7fb9','#8d9a93'],
    grid:{left:48,right:16,top:32,bottom:28},legend:{top:0,textStyle:{color:'#505c6b',fontSize:11}},
    tooltip:{trigger:'axis',valueFormatter:v=>v==null?'—':`${Number(v).toFixed(2)}%`},
    xAxis:{type:'category',boundaryGap:false,data:result.points.map(p=>p.date),axisLabel:{formatter:v=>v.slice(5)}},
    yAxis:{type:'value',scale:true,axisLabel:{formatter:'{value}%'},splitLine:{lineStyle:{color:'#eef0f3'}}},
    series:[['iv','合约收盘 IV'],['hv20','指数 HV20']].map(([key,name])=>({name,type:'line',showSymbol:false,connectNulls:false,data:result.points.map(p=>p[key]==null?null:p[key]*100)}))}),[result]);
  const latest=result?.latest, gap=latest?.iv!=null&&latest?.hv20!=null?(latest.iv-latest.hv20)*100:null;
  return <section className="options-surface options-iv-history">
    <div className="options-heading"><h2>隐含波动率 · 历史位置</h2><div className="options-analysis-tools"><label>合约<select value={id||''} onChange={e=>setCode(e.target.value)}>{ordered.map(r=><option key={r.code} value={r.code}>{r.code}</option>)}</select></label><label>观察窗口<select value={window} onChange={e=>setWindow(Number(e.target.value))}><option value={20}>近20个交易日</option><option value={60}>近60个交易日</option></select></label></div></div>
    <div className="options-iv-layout"><div className="options-iv-stats">
      <Metric label="截面 IV" value={selected?.iv==null?'—':`${fmt(selected.iv*100,2)}%`} hint={'由买卖中价反解的隐含波动率。'}/>
      <Metric label="收盘 IV" value={latest?.iv==null?'—':`${fmt(latest.iv*100,2)}%`} hint={'由收盘价反解；利率1.5%，ACT/365。观测日 '+(latest?.date||'无')}/>
      <Metric label="历史分位" value={result?.percentile==null?'—':`${fmt(result.percentile)}%`} hint={'(低于当前IV的样本数＋相等数÷2) ÷ 有效样本数 ×100%；含当前值，'+(result?.n??0)+'/'+window+' 个有效交易日。'}/>
      <Metric label="IV − HV20" value={gap==null?'—':`${fmt(gap,2)} pp`} hint={'同日收盘IV − HV20（百分点）。HV20＝20日对数收益样本标准差×√252；两者期限不同。'}/>
    </div><div>{error?<p role="alert">{error}</p>:!result?<p role="status">读取已保存历史…</p>:result.points.length?<ChartFrame><div ref={ref} className="options-iv-sparkline" role="img" aria-label="所选合约历史隐含波动率与指数历史波动率"/></ChartFrame>:<p>{result.reason}</p>}</div></div>
    <p className="options-note">{result?.range_min!=null?`窗口 IV 范围 ${fmt(result.range_min*100,2)}%–${fmt(result.range_max*100,2)}%。`:''}</p>

  </section>;
}
export default function SurfaceComparison({ rows, months, metric }) {
  const chartRef = useRef(null);
  const data = useMemo(() => surfaceMetrics(rows, months), [rows, months]);
  const [hidden, setHidden] = usePreference('options.surface.hidden.'+metric,[]);
  const visible = data.filter(m => !hidden.includes(m.id));
  useChart(chartRef, (chart) => {
    const series = metric === 'smiles' ? visible.map(m => ({
      name: m.code, type: 'line', connectNulls: false, showSymbol: true, symbolSize: 4,
      data: m.points.map(p => [p.x, p.iv === null ? null : p.iv * 100]),
      color: colors[data.findIndex(item => item.id === m.id) % colors.length],
    })) : [{name: metric === 'term' ? 'ATM IV' : '25Δ偏度', type: metric === 'term' ? 'line' : 'bar', connectNulls: false,
      data: visible.map(m => metric === 'term' ? (m.atm_iv === null ? null : m.atm_iv * 100) : ({value:m.skew25_pp, itemStyle:{color:m.skew25_pp >= 0 ? '#b64d49' : '#2b8167'}})), color:'#4e7fb9'}];
    chart.setOption({ animation:false, grid:{left:55,right:25,top:40,bottom:45}, legend:metric === 'smiles' ? {top:0} : undefined,
      tooltip:{trigger:'axis',valueFormatter:x => x == null ? '—' : `${Number(x).toFixed(2)}${metric === 'skew' ? ' pp' : '%'}`},
      xAxis:metric === 'smiles' ? {type:'value',scale:true,min:'dataMin',max:'dataMax',name:'K / F',nameLocation:'middle',nameGap:28,axisLabel:{formatter:x=>Number(x).toFixed(2)}} : {type:'category',data:visible.map(m=>m.code)},
      yAxis:{type:'value',scale:true,name:metric === 'skew' ? 'Put IV − Call IV (pp)' : 'IV (%)',splitLine:{lineStyle:{color:'#eef0f3'}}}, series });
  }, [data, hidden, metric]);
  return <section className="options-surface">
    <div className="options-analysis-tools">{data.map((m,i)=><button key={m.id} type="button" className="options-plain-button" aria-pressed={!hidden.includes(m.id)} style={{borderColor:hidden.includes(m.id)?'#ddd':colors[i%colors.length],opacity:hidden.includes(m.id)?.5:1}} onClick={()=>setHidden(current=>current.includes(m.id)?current.filter(id=>id!==m.id):[...current,m.id])}>{m.code.slice(0,-2)}<strong style={{color:colors[i%colors.length]}}>{m.code.slice(-2)}</strong></button>)}</div>
    <div ref={chartRef} className="options-vol-chart" aria-label="跨月波动率比较图" />
    {visible.some(m => m.forward === null) ? <p className="options-time-context">{visible.filter(m => m.forward === null).map(m => m.code).join('、')} 缺少可用隐含远期，IV 指标保留为空；有买卖报价不等于可计算 IV。</p> : null}
    <p className="options-note">微笑：K/F · OTM 报价 · 偏度：Put 25Δ IV − Call 25Δ IV</p>
    <div className="options-mini-table-wrap"><table className="options-mini-table"><thead><tr><th>月份</th><th>截面剩余日历 / 交易日</th><th>隐含远期 F</th><th>ATM IV</th><th>Call 25Δ IV</th><th>Put 25Δ IV</th><th>25Δ偏度 pp</th></tr></thead><tbody>{visible.map(m=><tr key={m.id}><td>{m.code.slice(0,-2)}<strong style={{color:colors[data.findIndex(x=>x.id===m.id)%colors.length]}}>{m.code.slice(-2)}</strong></td><td>{m.calendar_days ?? '—'} / {m.trading_days ?? '—'}</td><td>{fmt(m.forward)}</td><td>{m.atm_iv === null ? '—' : `${fmt(m.atm_iv*100)}%`}</td><td>{m.call25_iv === null ? '—' : `${fmt(m.call25_iv*100)}%`}</td><td>{m.put25_iv === null ? '—' : `${fmt(m.put25_iv*100)}%`}</td><td>{fmt(m.skew25_pp,2)}</td></tr>)}</tbody></table></div>

  </section>;
}
