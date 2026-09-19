import {useEffect,useRef,useState} from 'react';
import * as ec from 'echarts/core';
import {MarkLineComponent,MarkPointComponent,TitleComponent} from 'echarts/components';
ec.use([MarkLineComponent,MarkPointComponent,TitleComponent]);
const fmt=v=>v==null?'—':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:2});

export default function SpreadPanel({data,start,revision}) {
 const [span,setSpan]=useState(start?'custom':'3m'),[ma,setMa]=useState(false),[legs,setLegs]=useState(false),[rank,setRank]=useState(false),[result,setResult]=useState(null),[error,setError]=useState('');
 const ref=useRef();
 useEffect(()=>{
  const abort=new AbortController();setResult(null);setError('');
  const qs=new URLSearchParams({product:data.product,near:data.near,far:data.far,price_type:data.price_type,end:data.meta.as_of,span,...(span==='custom'&&start?{start}:{})});
  fetch('/api/spread-history?'+qs,{signal:abort.signal}).then(async r=>{const d=await r.json();if(!r.ok)throw Error(d.detail||'价差历史读取失败');return d;}).then(setResult).catch(e=>{if(e.name!=='AbortError')setError(e.message);});
  return()=>abort.abort();
 },[data.product,data.near,data.far,data.price_type,data.meta.as_of,start,span,revision]);
 useEffect(()=>{
  if(!result?.series.length||!ref.current)return;
  const c=ec.init(ref.current,{textStyle:{fontFamily:getComputedStyle(ref.current).fontFamily},tooltip:{textStyle:{fontFamily:getComputedStyle(ref.current).fontFamily}}}), rows=result.series, dates=rows.map(r=>r.trade_date);
  const panels=[{title:'价差 / 原始报价单位',keys:[['spread',`${data.near} − ${data.far}`],...(ma?[['ma20','20期均线']]:[])]},
   ...(legs?[{title:'两腿价格 / 原始报价单位',keys:[['near_price',data.near],['far_price',data.far]]}]:[]),
   ...(rank?[{title:'历史分位 / %（不含当日）',keys:[['historical_percentile','历史分位']]}]:[])];
  const series=panels.flatMap((panel,i)=>panel.keys.map(([key,name])=>({name,type:'line',xAxisIndex:i,yAxisIndex:i,
   data:rows.map(r=>r[key]),connectNulls:false,showSymbol:rows.length<20,symbolSize:5,
   ...(key==='spread'?{markLine:{silent:true,symbol:'none',label:{formatter:'零轴'},data:[{yAxis:0}]},
    markPoint:{symbol:'circle',symbolSize:7,label:{show:true,position:'left',formatter:p=>'最新 '+fmt(p.value)},
     data:rows.flatMap((r,j)=>r.spread==null?[]:[{coord:[j,r.spread],value:r.spread}]).slice(-1)}}:{})})));
  c.setOption({animation:false,color:['#2563eb','#d97706','#0d9488','#9333ea','#64748b'],
   tooltip:{trigger:'axis',formatter:params=>{
    const point=rows[params[0]?.dataIndex];if(!point)return '';
    return [point.trade_date,...panels.flatMap(p=>p.keys.map(([key,name])=>
     `${name}：${fmt(point[key])}${key==='historical_percentile'&&point[key]!=null?'%':''}`))].join('<br/>');
   }},axisPointer:{link:[{xAxisIndex:'all'}]},
   title:panels.map((p,i)=>({text:p.title,left:12,top:10+i*240,textStyle:{fontSize:12,color:'#64748b',fontWeight:400}})),
   legend:{bottom:0},grid:panels.map((_,i)=>({left:80,right:40,top:40+i*240,height:170})),
   xAxis:panels.map((_,i)=>({type:'category',data:dates,gridIndex:i,boundaryGap:false})),
   yAxis:panels.map((p,i)=>({type:'value',gridIndex:i,scale:true,...(p.keys[0][0]==='historical_percentile'?{min:0,max:100}:{}),axisLabel:{formatter:fmt}})),
   dataZoom:[{type:'inside',xAxisIndex:panels.map((_,i)=>i)},{type:'slider',xAxisIndex:panels.map((_,i)=>i),bottom:27,height:17}],series});
  const ro=new ResizeObserver(()=>c.resize());ro.observe(ref.current);return()=>{ro.disconnect();c.dispose();};
 },[result,ma,legs,rank,data.near,data.far]);
 return <section className="panel" id="spread-history"><h2>所选合约对 · 价差时间序列</h2>
  <p className="sub">{data.near} − {data.far} · {data.price_type==='close'?'收盘价':'结算价'} · 下方范围仅作用于本组图，不改变顶部风险与成本窗口</p>
  <div className="scan-controls">{[['1m','近1月'],['3m','近3月'],['1y','近1年'],['all','共同有效历史'],...(start?[['custom','顶部日期窗口']]:[])].map(([v,label])=><button key={v} aria-pressed={span===v} className={span===v?'primary':''} onClick={()=>setSpan(v)}>{label}</button>)}
   {[[ma,setMa,'20期均线'],[legs,setLegs,'两腿价格联动'],[rank,setRank,'历史分位']].map(([v,fn,label])=><label className="check" key={label}><input type="checkbox" checked={v} onChange={e=>fn(e.target.checked)}/>{label}</label>)}
  </div>
  {error?<p role="alert">{error}</p>:!result?<p role="status">读取所选两腿历史…</p>:<><p className="sub">有效配对 {result.first||'无'} 至 {result.last||'无'} · {result.paired_count}个样本 · 本地共同历史 {result.common_first||'无'} 至 {result.common_last||'无'}</p>
   {!result.paired_count?<p className="empty">此范围无有效配对，尝试共同有效历史。</p>:<div ref={ref} style={{height:280+240*(Number(legs)+Number(rank))}} role="img" aria-label={`${data.near}减${data.far}价差时间序列，${result.paired_count}个有效配对；最新值${fmt(result.series.filter(r=>r.spread!=null).at(-1)?.spread)}`}/>}
   {result.comparable&&<div className="comparable"><h3>同月份组合 · 相近日历季节核查</h3>
    <p className="sub">独立于上方缩放范围，每个历史交割年仅取一个真实共同观测；日历季节相近，不是准确到期调整，不自动改变雷达方向。</p>
    <p>比较当前值 {fmt(result.comparable.current?.spread)}（{result.comparable.current?.trade_date||'无共同观测'}） · 历史 {result.comparable.samples.length} 年 · 中位数 {fmt(result.comparable.median)} · 分位 {result.comparable.percentile==null?'不足3年，暂不报告':fmt(result.comparable.percentile)+'%'}{result.comparable.current_stale?' · 当前对子观测落后截止日':''}</p>
    <details><summary>查看逐年样本及匹配规则</summary><p>{result.comparable.window_rule}</p><div className="table-wrap"><table><thead><tr><th>历史组合</th><th>实际观测日</th><th className="num">距目标 / 日</th><th className="num">价差</th></tr></thead><tbody>{result.comparable.samples.map(p=><tr key={p.year}><td>{p.near} / {p.far}</td><td>{p.trade_date}</td><td className="num">{p.distance_days}</td><td className="num">{fmt(p.spread)}</td></tr>)}</tbody></table></div><ul>{result.comparable.warnings.map(w=><li key={w}>{w}</li>)}</ul></details>
   </div>}
   <details><summary>日期、均线及缺口口径</summary><ul>{result.warnings.map(w=><li key={w}>{w}</li>)}</ul></details></>}
 </section>;
}
