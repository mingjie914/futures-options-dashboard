import { conditionalFetch as fetch } from '../../../frontend/src/transport.js';
import { useChart, usePreference } from '../../../frontend/src/charts.js';
import {useEffect,useRef,useState} from 'react';
const fmt=v=>v==null?'—':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:2});

export default function SpreadPanel({data,revision,controls,beforeChart}) {
 const [span,setSpan]=usePreference('futures.spread.span.default','3m'),[ma,setMa]=usePreference('futures.spread.ma',false),[legs,setLegs]=usePreference('futures.spread.legs',false),[rank,setRank]=usePreference('futures.spread.rank',false),[result,setResult]=useState(null),[error,setError]=useState('');
 const ref=useRef();
 useEffect(()=>{
  const abort=new AbortController();setResult(null);setError('');
  const qs=new URLSearchParams({product:data.product,near:data.near,far:data.far,price_type:data.price_type,end:data.meta.as_of,span});
  fetch('/api/spread-history?'+qs,{signal:abort.signal}).then(async r=>{const d=await r.json();if(!r.ok)throw Error(d.detail||'价差历史读取失败');return d;}).then(setResult).catch(e=>{if(e.name!=='AbortError')setError(e.message);});
  return()=>abort.abort();
 },[data.product,data.near,data.far,data.price_type,data.meta.as_of,span,revision]);
 useChart(ref,c=>{
  const rows=result.series, dates=rows.map(r=>r.trade_date);
  const panels=[{title:'价差 / 原始报价单位',keys:[['spread',`${data.near} − ${data.far}`],...(ma?[['ma20','20期均线']]:[])]},
   ...(legs?[{title:'两腿价格 / 原始报价单位',keys:[['near_price',data.near],['far_price',data.far]]}]:[]),
   ...(rank?[{title:'历史分位 / %（不含当日）',keys:[['historical_percentile','历史分位']]}]:[])];
  const series=panels.flatMap((panel,i)=>panel.keys.map(([key,name])=>({name,type:'line',xAxisIndex:i,yAxisIndex:i,
   data:rows.map(r=>r[key]),connectNulls:false,showSymbol:rows.length<20,symbolSize:5,
   ...(key==='spread'?{markLine:{silent:true,symbol:'none',label:{formatter:'零轴'},data:[{yAxis:0}]},
    markPoint:{symbol:'circle',symbolSize:7,label:{show:true,position:'left',formatter:p=>'最新 '+fmt(p.value)},
     data:rows.flatMap((r,j)=>r.spread==null?[]:[{coord:[j,r.spread],value:r.spread}]).slice(-1)}}:{})})));
  c.setOption({animation:false,color:['#4e7fb9','#a48b59','#568f84','#8c7b9f','#505c6b'],
   tooltip:{trigger:'axis',formatter:params=>{
    const point=rows[params[0]?.dataIndex];if(!point)return '';
    return [point.trade_date,...panels.flatMap(p=>p.keys.map(([key,name])=>
     `${name}：${fmt(point[key])}${key==='historical_percentile'&&point[key]!=null?'%':''}`))].join('<br/>');
   }},axisPointer:{link:[{xAxisIndex:'all'}]},
   title:panels.map((p,i)=>({text:p.title,left:12,top:30+i*400,textStyle:{fontSize:12,color:'#505c6b',fontWeight:400}})),
   legend:{type:'scroll',top:0},grid:panels.map((_,i)=>({left:80,right:40,top:60+i*400,height:310})),
   xAxis:panels.map((_,i)=>({type:'category',data:dates,gridIndex:i,boundaryGap:false})),
   yAxis:panels.map((p,i)=>({type:'value',gridIndex:i,scale:true,...(p.keys[0][0]==='historical_percentile'?{min:0,max:100}:{}),axisLabel:{formatter:fmt}})),
   dataZoom:[{type:'inside',xAxisIndex:panels.map((_,i)=>i)},{type:'slider',xAxisIndex:panels.map((_,i)=>i),bottom:8,height:17}],series});

 },[result,ma,legs,rank,data.near,data.far]);
 const spanControl=<label>跨度<select aria-label="历史跨度" value={span} onChange={e=>setSpan(e.target.value)}>{[['1m','近1月'],['3m','近3月'],['1y','近1年'],['all','共同有效历史']].map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>;
 return <section className="panel" id="spread-history"><div className="filters">{controls(spanControl)}
   {[[ma,setMa,'20期均线'],[legs,setLegs,'两腿价格联动'],[rank,setRank,'历史分位']].map(([v,fn,label])=><label className="check" key={label}><input type="checkbox" checked={v} onChange={e=>fn(e.target.checked)}/>{label}</label>)}
  </div>
  {beforeChart}
  {error?<p role="alert">{error}</p>:!result?<p role="status">读取所选两腿历史…</p>:<><p className="sub">有效配对 {result.first||'无'} 至 {result.last||'无'} · {result.paired_count}个样本</p>
   {!result.paired_count?<p className="empty">此范围无有效配对，尝试共同有效历史。</p>:<div ref={ref} style={{height:440+400*(Number(legs)+Number(rank))}} role="img" aria-label={`${data.near}减${data.far}价差时间序列，${result.paired_count}个有效配对；最新值${fmt(result.series.filter(r=>r.spread!=null).at(-1)?.spread)}`}/>}
   </>}
 </section>;
}
