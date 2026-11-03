import {Metric,usePreference,observedRows, useResource,RefreshButton,useChart,ChartFrame} from '../../../frontend/src/charts.js';
import {useMemo,useRef,useState} from 'react';
import {contractColors} from './lineSegments.mjs';
const fmt=v=>v==null?'—':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:2});
const weak=(p,k)=>p?.['near_'+k]==null||p?.['far_'+k]==null?null:Math.min(p['near_'+k],p['far_'+k]);
export const passesScan=(o,minHistory=0,minVolume=0)=>(minHistory<=0||(o.latest?.history_n??-1)>=minHistory)&&(minVolume<=0||(weak(o.latest,'volume')??-1)>=minVolume);
// Object grouping, family filtering and direction are owned by the API.
export const radarRows=data=>data?.objects||[];

export function ObservationDate({latest,value,...props}) {
 const day=value||latest||'';
 return <label className="observation-date"><input aria-label="观察日" title="观察日；清空恢复最新" type="date" {...props} value={day} onClick={e=>e.currentTarget.showPicker?.()}/></label>;
}

export function BasisPanel({product,end,priceType,revision,Chart,onProduct,controls}) {
 const [symbol,setSymbol]=usePreference('futures.basis.'+product+'.symbol','');
 const indexProducts=['IM','IF','IC','IH'],supported=indexProducts.includes(product);
 const {data,error,busy}=useResource(supported?'/api/basis-reference?'+new URLSearchParams({product,price_type:priceType,all_contracts:true,...(end?{end}:{})}):null,revision);
 const zoomGroup=useMemo(()=>new Set(),[data]);
 const colors=useMemo(()=>({...contractColors((data?.contracts||[]).map(symbol=>({symbol,position:data.contract_positions?.[symbol]}))),[data?.reference?.name||'指数']:'#505c6b'}),[data]);
 const contracts=Object.keys(colors).filter(s=>data?.contracts?.includes(s));
 const chosen=data?.comparisons?.[symbol]?symbol:data?.symbol,selected=data?.comparisons?.[chosen]||data;
 const last=selected?.latest,isIndex=data?.status==='index_close_basis',unit=isIndex?'点':'元/吨',label=isIndex?'基差':'周频参考价差';
 const chartLabel=isIndex?'股指基差':label;
 const rows=useMemo(()=>{
  const days=new Map();
  for(const [s,result] of Object.entries(data?.comparisons||{}))for(const p of result.series){
   if(!days.has(p.trade_date))days.set(p.trade_date,{trade_date:p.trade_date,reference_price:p.reference_price});
   Object.assign(days.get(p.trade_date),{[s]:p.reference_spread,[s+'_annual']:p.annualized_basis_ratio,[s+'_price']:p.futures_price});
  }
  return [...days.values()].sort((a,b)=>a.trade_date.localeCompare(b.trade_date));
 },[data]);
 const contract=data?.contracts?.length?<select aria-label="重点合约" value={chosen} onChange={e=>setSymbol(e.target.value)}>{data.contracts.map(s=><option key={s}>{s}</option>)}</select>:null;
 return <><div className="filters page-filters">{controls?.(contract,data?.meta?.as_of,busy)}</div>
 {!supported||data?.status==='unavailable'?<section className="panel" aria-busy={busy}><p className="sub">{supported?'所选品种暂无基差数据':['T','TF','TS','TL'].includes(product)?'国债基差暂未接入':'商品基差暂未接入'}</p><div className="scan-controls">{indexProducts.map(p=><button key={p} onClick={()=>onProduct(p)}>{p}</button>)}</div></section>:error?<><p role="alert">{error}</p>{symbol&&<button onClick={()=>setSymbol('')}>重选活跃合约</button>}</>:!data?<p role="status">读取基差…</p>:<>
 <section className="panel" aria-busy={busy}>
 <div className="stats-heading"><h2>{chartLabel}{!isIndex&&' / '+unit}</h2><span>{last?.trade_date||'无有效配对'} · {selected.matched} 个观测{selected.unmatched?' · '+selected.unmatched+' 个未配对':''}{data.meta.stale?' · 缓存降级':''}{data.reference.source?.includes('测试')?' · 测试样本':''}</span></div>
 <div className="metrics"><Metric label={label} value={fmt(last?.reference_spread)} hint="现货 − 期货"/><Metric label={isIndex?'指数':'参考价'} value={fmt(last?.reference_price)}/><Metric label="期货价" value={fmt(last?.futures_price)}/>{isIndex&&<Metric label="年化基差率" value={last?.annualized_basis_ratio==null?'—':fmt(last.annualized_basis_ratio*100)+'%'} hint="基差 ÷ 现货 × 365 ÷ 距交割日天数"/>}</div>
 {rows.length?<Chart zoomGroup={zoomGroup} rows={rows} lines={contracts.map(s=>[s,s])} colors={colors} focus={chosen}/>:<p className="empty">暂无配对数据</p>}
 </section>
 {isIndex&&!!rows.length&&<section className="panel" aria-busy={busy}><h2>年化基差率 / %</h2><Chart zoomGroup={zoomGroup} rows={rows} lines={contracts.map(s=>[s+'_annual',s])} colors={colors} percent focus={chosen}/></section>}
 {!!data.series.length&&<section className="panel basis-prices" aria-busy={busy}><h2>{isIndex?'期现价格':'价格对比 / '+unit}</h2><Chart zoomGroup={zoomGroup} rows={rows} lines={[["reference_price",data.reference.name],...contracts.map(s=>[s+'_price',s])]} colors={colors} focus={chosen}/></section>}
 </>}
 </>;
}

export function SpreadDirection({direction}) {
 return direction==='buy'||direction==='sell'?<span className="spread-direction"><span className={direction==='buy'?'direction-long':'direction-short'}>{direction==='buy'?'多近':'空近'}</span><span className={direction==='buy'?'direction-short':'direction-long'}>{direction==='buy'?'空远':'多远'}</span></span>:<span className="sub">—</span>;
}

export function HistoricalPosition({event}) {
 const p=event.percentile;
 if(p==null||!Number.isFinite(p))return null;
 const basis=event.kind==='spread_level'?'价差水平':'变动绝对值';
 return <div className="historical-position"><span className="percentile-track" aria-hidden="true"><i style={{width:Math.max(0,Math.min(100,p))+'%'}}/></span><Metric label={fmt(p)+'%'} hint={basis+'分位；此前 '+event.n+' 个有效样本，不含当日。'}/></div>;
}

export function PositionPeaks({end,minVolume,minPosition,revision,priceType,onSelect}) {
 const {data,error,busy}=useResource('/api/position-peaks?'+new URLSearchParams({...(end?{end}:{}),min_volume:minVolume,min_position:minPosition}),revision);
 return <section className="position-ranking" aria-busy={busy}><div className="ranking-heading"><h2>持仓异常 TOP10</h2>{data&&<span className="sub">{data.as_of}</span>}</div>
 {error?<p role="alert">{error}</p>:!data?<p role="status">读取历史持仓峰值…</p>:!data.items.length?<p className="empty">暂无可比较的历史持仓</p>:<div className="table-wrap"><table className="position-peaks"><colgroup>{[5,8,15,14,18,26,14].map((w,i)=><col key={i} style={{width:w+'%'}}/>)}</colgroup><thead><tr><th>排名</th><th>品种</th><th>名称</th><th><Metric label="当前主力" hint="当日持仓最多的月合约，按顶部量仓门槛筛选；并列时按合约代码排序。"/></th><th className="num">当前持仓 / 手</th><th className="num"><Metric label="过去5年同月峰值 / 手" hint="前5个交割年份同月合约的最高持仓，不含当前合约；不足5年按已有样本。"/></th><th className="num"><Metric label="偏离" hint="（当前持仓 ÷ 历史峰值 − 1）×100%，降序排列。"/></th></tr></thead><tbody>{data.items.map((r,i)=><tr key={r.product}><td>{i+1}</td><td>{r.product}</td><td>{r.name||'—'}</td><td><button className="link" onClick={()=>onSelect(r.product,{near:r.symbol},{price_type:priceType,as_of:data.as_of},'持仓换月')}>{r.symbol}</button></td><td className="num">{fmt(r.position)}</td><td className="num"><Metric label={fmt(r.baseline_peak)} hint={'峰值合约 '+r.baseline_symbol+'；有效 '+r.history_years.length+'/5 年：'+r.history_years.join('、')}/>{r.history_years.length<5&&<small className="peak-years">{r.history_years.length}年</small>}</td><td className={'num '+(r.deviation>0?'direction-long':r.deviation<0?'direction-short':'')}>{r.deviation>0?'+':''}{fmt(r.deviation)}%</td></tr>)}</tbody></table></div>}
 </section>;
}

export function RadarPanel({onSelect}) {
 const [draft,setDraft]=usePreference('futures.radar.draft',{end:'',min_volume:1000,min_position:1000});
 const [family,setFamily]=usePreference('futures.radar.family','all');
 const [query,setQuery]=usePreference('futures.radar.query',{min_volume:1000,min_position:1000}),[revision,setRevision]=useState(0),[filter,setFilter]=usePreference('futures.radar.filter','priority'),[search,setSearch]=usePreference('futures.radar.search','');
 const {data,error,busy}=useResource('/api/radar?'+new URLSearchParams({...query,family,price_type:'close'}),revision);
 const items=radarRows(data).filter(r=>r.product.includes(search.trim().toUpperCase())&&(filter==='all'||filter==='signals'&&r.events.length>0&&r.bucket!=='数据待检查'||filter==='priority'&&r.bucket==='优先核查'||filter==='watch'&&r.bucket==='观察名单'||filter==='quality'&&(r.quality.length>0||r.bucket==='数据待检查')));
 const limits=r=>r.limits.filter(v=>!['均值回归假设；到期、成本与可成交性待核验','仅凭量仓或价差突变不判断多空','短样本'].includes(v));
 const hasLimits=items.some(r=>limits(r).length);
 const change=e=>setDraft(p=>({...p,[e.target.name]:e.target.value}));
 function open(r){const e=r.events.find(e=>e.family==='spread')||r.events[0];onSelect(r.product,e?{...e,direction_code:r.direction_code,rationale:r.events.map(e=>e.label).join('；')}:null,data,e?e.far?'跨期分析':e.family==='position'?'持仓换月':'机会扫描':'机会扫描');}
 return <section className="panel radar-panel" aria-busy={busy}>
 <form className="filters" onSubmit={e=>{e.preventDefault();const v={...Object.fromEntries(new FormData(e.currentTarget)),end:draft.end};if(!v.end)delete v.end;setQuery(v);setRevision(revision+1);}}>
 <ObservationDate name="end" value={draft.end} onChange={change} latest={data?.as_of}/><label>成交 ≥<input aria-label="每腿最低成交 / 手" type="number" name="min_volume" min="0" max="10000000" step="1" defaultValue={draft.min_volume} onChange={change} required/></label><label>持仓 ≥<input aria-label="每腿最低持仓 / 手" type="number" name="min_position" min="0" max="10000000" step="1" defaultValue={draft.min_position} onChange={change} required/></label><RefreshButton busy={busy} type="submit"/></form>
 <PositionPeaks minVolume={query.min_volume} minPosition={query.min_position} end={query.end} revision={revision} priceType="close" onSelect={onSelect}/>{error&&<p role="alert">扫描失败：{error}</p>}
 {data&&<>
 <h2>量价异常</h2><div className="scan-controls"><label>异常类型<select value={family} onChange={e=>setFamily(e.target.value)}><option value="all">全部类型</option><option value="position">持仓异常</option><option value="spread">价差异常</option><option value="volume">成交异常</option></select></label><label>显示<select value={filter} onChange={e=>setFilter(e.target.value)}><option value="priority">优先核查</option><option value="watch">观察名单</option><option value="signals">全部异常</option><option value="quality">数据待检查</option><option value="all">全部已扫描</option></select></label><label>品种<input value={search} onChange={e=>setSearch(e.target.value)} placeholder="如 IM、CU"/></label><span>{items.length} 项 · {new Set(items.map(r=>r.product)).size} 品种</span><span className="radar-date" title={`已扫描 ${data.scanned} / ${data.expected} 品种；查询截止 ${data.requested_end}`}>{data.as_of||'无观测'}{data.scanned<data.expected?` · 已扫描 ${data.scanned}/${data.expected}`:''}</span></div>
 <div className={'table-wrap'+(items.length<=10?' short-results':'')}><table className={"radar-events"+(hasLimits?" radar-with-notes":"")}><thead><tr><th>合约</th><th><Metric label="方向参考" hint="价差均值回归参考，未计季节、到期及成本；红多、绿空。"/></th><th><div className="radar-anomaly-heading"><span>异常</span><span>历史位置</span></div></th>{hasLimits&&<th>备注</th>}</tr></thead><tbody>{items.map(r=><tr key={r.key}>
 <td><button className="link" onClick={()=>open(r)}>{r.near?r.near+(r.far?' / '+r.far:''):r.product+' 合计'}</button>{!['priority','watch'].includes(filter)&&<small>{r.bucket}</small>}{r.as_of!==data.as_of&&<small>观测 {r.as_of||'缺失'}</small>}</td>
 <td><SpreadDirection direction={r.direction_code}/></td>
 <td>{r.events.length?r.events.map((e,i)=><div className="radar-evidence" key={i}><Metric label={e.label} value={fmt(e.value)} hint={`基准 ${e.n} 个样本${e.weak_volume!=null?'；较弱腿成交 '+fmt(e.weak_volume)+' 手':''}${e.weak_position!=null?'；较弱腿持仓 '+fmt(e.weak_position)+' 手':''}`}/><HistoricalPosition event={e}/>{e.short&&<span>短样本</span>}</div>):<span className="sub">未命中</span>}</td>
 {hasLimits&&<td>{limits(r).map((v,i)=><small key={i}>{v}</small>)}</td>}
 </tr>)}</tbody></table></div>
 {!items.length&&<p className="empty">当前筛选无结果。</p>}
 {data.failures.length>0&&<div role="alert"><h3>{data.failures.length} 个品种读取失败</h3><ul>{data.failures.map(f=><li key={f.product}>{f.product}：{f.reason}</li>)}</ul></div>}
 </>}
 </section>;
}

export function Trend({points=[]}) {
 const values=points.map(p=>p.spread).filter(Number.isFinite);
 if(!values.length)return <span>无走势样本</span>;
 const min=Math.min(...values),max=Math.max(...values),segments=[];let segment=[];
 points.forEach((p,i)=>{if(!Number.isFinite(p.spread)){if(segment.length)segments.push(segment);segment=[];}else segment.push(`${3+i*114/Math.max(1,points.length-1)},${max===min?20:36-(p.spread-min)/(max-min)*32}`);});
 if(segment.length)segments.push(segment);
 return <svg width="120" height="40" viewBox="0 0 120 40" role="img" aria-label={`近期价差趋势，${values.length}个有效配对`}><title>{`${points[0]?.trade_date} 至 ${points.at(-1)?.trade_date}；独立纵轴，仅比较走势`}</title>{segments.map((s,i)=>s.length===1?<circle key={i} cx={s[0].split(',')[0]} cy={s[0].split(',')[1]} r="2" fill="#4e7fb9"/>:<polyline key={i} points={s.join(' ')} fill="none" stroke="#4e7fb9" strokeWidth="1.7"/>)}</svg>;
}

export function ScanPanel({data,onPair}) {
 const [sort,setSort]=usePreference('futures.scan.sort','month'),[only,setOnly]=usePreference('futures.scan.only',false);
 const [minHistory,setMinHistory]=usePreference('futures.scan.minHistory',0),[minVolume,setMinVolume]=usePreference('futures.scan.minVolume',0);
 const rows=data.opportunities.filter(o=>passesScan(o,minHistory,minVolume)&&(!only||(o.latest?.historical_percentile!=null&&(o.latest.historical_percentile<=10||o.latest.historical_percentile>=90))));
 const score=o=>sort==='volume'?weak(o.latest,'volume'):sort==='position'?weak(o.latest,'position'):o.latest?.historical_percentile==null?null:Math.abs(o.latest.historical_percentile-50);
 if(sort!=='month')rows.sort((a,b)=>(score(b)??-Infinity)-(score(a)??-Infinity));
 return <section className="panel scan-panel"><h2>相邻合约</h2>
 <div className="scan-controls"><label>排序<select value={sort} onChange={e=>setSort(e.target.value)}><option value="month">合约月份</option><option value="extreme">分位偏离中位数</option><option value="volume">较弱腿成交量</option><option value="position">较弱腿持仓量</option></select></label><label className="check"><input type="checkbox" checked={only} onChange={e=>setOnly(e.target.checked)}/>仅看分位 ≤10% 或 ≥90%</label><label>历史样本至少<input type="number" min="0" step="1" value={minHistory} onChange={e=>setMinHistory(Math.max(0,Number(e.target.value)))}/></label><label>较弱腿成交至少 / 手<input type="number" min="0" step="1" value={minVolume} onChange={e=>setMinVolume(Math.max(0,Number(e.target.value)))}/></label><span>{rows.length} / {data.opportunities.length} 个组合</span></div>

 <div className="table-wrap"><table><thead><tr>{['组合 / 配对日期','价差','前次变动','走势 / 60观测','历史分位','分位样本 / 配对总数','较弱腿成交 / 手','较弱腿持仓 / 手','备注'].map((h,i)=><th className={[1,2,4,5,6,7].includes(i)?'num':undefined} key={h}>{i===4?<Metric label={h} hint="此前最多252个有效价差的分位，不含当日。"/>:h}</th>)}</tr></thead><tbody>{rows.map(o=>{const p=o.latest;const flags=[!p?'无配对数据':p.trade_date!==data.meta.as_of?'配对日期落后':null,p?.historical_percentile==null?'分位不足':null,weak(p,'volume')==null||weak(p,'position')==null?'量仓缺失':null,data.meta.stale?'缓存降级':null].filter(Boolean);return <tr key={o.near+o.far}><td><button className="link" onClick={()=>onPair(o.near,o.far)}>{o.near} / {o.far}</button><small>{p?.trade_date||'—'}</small></td><td className="num">{fmt(p?.spread)}</td><td className="num">{fmt(p?.change)}</td><td><Trend points={o.trend}/></td><td className="num">{p?.historical_percentile==null?'—':fmt(p.historical_percentile)+'%'}</td><td className="num">{fmt(p?.history_n)} / {fmt(o.sample_count)}</td><td className="num">{fmt(weak(p,'volume'))}</td><td className="num">{fmt(weak(p,'position'))}</td><td>{flags.join(' · ')||'—'}</td></tr>;})}</tbody></table></div>{!rows.length&&<p className="empty">当前筛选无组合；可取消分位筛选。</p>}</section>;
}

export {SpreadMatrix,spreadMatrix} from '../../../frontend/src/SpreadMatrix.jsx';

export function RolloverProgress({data}) {
 const ref=useRef(),rounds=useMemo(()=>(data?.rounds||[]).map(r=>({...r,series:observedRows(r.series,['outgoing','incoming','spread'])})),[data]),available=rounds.some(r=>r.series.length);
 const firstDay=Math.min(0,...rounds.flatMap(r=>r.series.length?[r.series[0].day]:[]));
 useChart(ref,chart=>chart.setOption({animation:false,
  legend:{type:'scroll',top:0},grid:{left:75,right:65,top:60,bottom:72},
  tooltip:{trigger:'axis',confine:true,renderMode:'richText',formatter:params=>{
   const visible=params.filter(p=>p.value[1]!=null),lines=[`距最后交易日 ${Math.abs(params[0]?.value[0])} 天`];
   for(const previous of [false,true]){
    const group=visible.filter(p=>p.seriesName.endsWith(' · 上轮')===previous);
    if(group.length)lines.push(`${previous?'上轮':'本轮'} ${group[0].data.trade_date}`,...group.filter(p=>p.seriesName!=='价差').map(p=>`${p.seriesName.replace(' · 上轮','')}：${fmt(p.value[1])} 手`));
   }
   const spread=visible.find(p=>p.seriesName==='价差');
   if(spread)lines.push(`价差：${fmt(spread.value[1])}`);
   return lines.join('\n');
  }},
  xAxis:{type:'value',min:'dataMin',max:0,name:'距最后交易日 / 天',nameLocation:'middle',nameGap:28,axisLabel:{hideOverlap:true}},
  yAxis:[{type:'value',position:'left',axisLine:{onZero:false,show:false},name:'持仓 / 手',splitLine:{lineStyle:{color:'#eef0f3'}}},{type:'value',position:'right',axisLine:{onZero:false,show:false},name:'价差',scale:true,splitLine:{show:false}}],
  dataZoom:[{type:'inside',startValue:Math.max(-180,firstDay),endValue:0},{type:'slider',bottom:4,height:18,startValue:Math.max(-180,firstDay),endValue:0}],
  series:rounds.filter(r=>r.series.length).flatMap(r=>['outgoing','incoming',...(r.name==='current'?['spread']:[])].map((key,i)=>({
   name:key==='spread'?'价差':r[key]+(r.name==='previous'?' · 上轮':''),type:'line',yAxisIndex:key==='spread'?1:0,showSymbol:false,connectNulls:false,
   lineStyle:{type:r.name==='previous'?'dashed':'solid',width:key==='spread'?1.8:2},itemStyle:{color:['#4e7fb9','#a48b59','#222222'][i]},
   data:r.series.map(p=>({value:[p.day,p[key]],trade_date:p.trade_date}))
  }))).sort((a,b)=>a.yAxisIndex-b.yAxisIndex)
 }, true),[data]);
 return <section className="panel"><h2>移仓对比</h2>{available?<ChartFrame><div className="chart" ref={ref} role="img" aria-label="本轮与上一轮移仓持仓及价差，按距移出合约最后交易日的自然日对齐"/></ChartFrame>:<p className="empty">主力映射或最后交易日尚未齐备</p>}{available&&rounds.some(r=>!r.series.length)&&<p className="sub">上轮或本轮资料不全</p>}</section>;
}

export function SeasonalSpreadChart({summary}) {
 const ref=useRef(),samples=summary.samples.filter(p=>Number.isFinite(p.value)),current=summary.current;
 const rows=[...samples,...(Number.isFinite(current?.value)?[{...current,current:true}]:[])].sort((a,b)=>b.year-a.year);
 useChart(ref,chart=>chart.setOption({
  grid:{left:66,right:60,top:4,bottom:24},
  tooltip:{trigger:'item',confine:true,renderMode:'richText',formatter:p=>{const row=rows[p.dataIndex];return `${row.label}\n${row.trade_date}：${fmt(row.value)}`;}},
  xAxis:{type:'value',scale:true,axisLabel:{hideOverlap:true},splitLine:{lineStyle:{color:'#eef0f3'}}},
  yAxis:{type:'category',inverse:true,axisLabel:{interval:0},data:rows.map(p=>`${p.year}${p.current?' 当前':''}`),axisLine:{show:false},axisTick:{show:false}},
  series:[{type:'line',showSymbol:true,showAllSymbol:true,lineStyle:{opacity:0},data:rows.map((p,i)=>({value:[p.value,i],symbol:p.current?'diamond':'circle',symbolSize:p.current?11:8,itemStyle:{color:p.current?'#4e7fb9':'#aebdce'},label:{show:true,position:'right',distance:6,formatter:()=>fmt(p.value),color:p.current?'#315f94':'#505c6b'}})),
   ...(Number.isFinite(summary.median)?{markLine:{silent:true,symbol:'none',label:{show:false},lineStyle:{color:'#7c8590',type:'dashed'},data:[{xAxis:summary.median}]}}:{})}]
 }, true),[summary]);
 return samples.length?<><div className="metrics"><Metric label="中位数" value={fmt(summary.median)} hint="同月日向前7日匹配，每年一个样本，不含当前交割年。"/><Metric label="偏离" value={fmt(Number.isFinite(current?.value)&&Number.isFinite(summary.median)?current.value-summary.median:null)} hint="当前价差 − 同期历史中位数。"/></div><div ref={ref} className="seasonal-spread-chart" role="img" aria-label="历年同期价差点图，当前年份在上，蓝色菱形为当前组合，虚线为历史中位数"/></>:<p className="empty">暂无同期历史样本</p>;
}

export function CurvePanel({data,onPair,Chart,controls,beforeChart}) {
 const curve=data.curve, comparisons=curve.comparisons||[];
 const rows=curve.nodes.map((n,i)=>({...n,...Object.fromEntries(comparisons.map(c=>['lag'+c.lag,c.nodes[i]?.price??null]))}));
 const tooltip=params=>{const row=rows[params[0]?.dataIndex];return row?[row.symbol,...params.map(p=>`${p.marker} ${p.seriesName}：${fmt(p.value)}`),`成交 ${fmt(row.volume)} 手 · 持仓 ${fmt(row.position)} 手`].join('<br/>'):'';};
 return <section className="panel curve-panel"><div className="filters">{controls}</div>
 {beforeChart}
 {rows.length?<Chart rows={rows} x="symbol" selected={[data.near,data.far]} tooltip={tooltip} lines={[["price",'当日 '+data.meta.as_of],...comparisons.filter(c=>c.as_of).map(c=>['lag'+c.lag,`前${c.lag}个观测日 ${c.as_of}`])]}/>:<p className="empty">当前日期暂无期限曲线数据</p>}
 {comparisons.filter(c=>!c.as_of).map(c=><p className="sub" key={c.lag}>前{c.lag}个观测日：窗口内观测不足</p>)}
 {!!curve.adjacent.length&&<div className="curve-pairs"><h2>相邻价差</h2>{curve.adjacent.map(p=><button className={p.near===data.near&&p.far===data.far?'selected':''} key={p.near+p.far} onClick={()=>onPair(p.near,p.far)} aria-label={`${p.near} − ${p.far}，价差 ${fmt(p.spread)}，查看价差季节性`}>{p.near} − {p.far}<strong>{fmt(p.spread)}</strong></button>)}</div>}
 </section>;
}
