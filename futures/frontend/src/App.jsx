import { useChart, ChartFrame, Metric, usePreference } from '../../../frontend/src/charts.js';
import {useEffect,useMemo,useRef,useState} from 'react';
import {ScanPanel,CurvePanel,RadarPanel,BasisPanel} from './MarketPanels.jsx';
import SpreadPanel from './SpreadPanel.jsx';
import HistoryPanel, {HistoryChart} from './HistoryPanel.jsx';
const fmt=v=>v==null?'—':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:2});
async function api(url,options){const r=await fetch(url,options),d=await r.json();if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:JSON.stringify(d.detail));return d;}
export function Chart({rows,lines,x='trade_date',percent=false,integer=false,seasonal=false,bar=false}){const ref=useRef();useChart(ref,c=>{const display=v=>v==null?'—':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:integer?0:2})+(percent?'%':'');c.setOption({color:['#4e7fb9','#568f84','#a48b59','#8c7b9f','#b36c73'],tooltip:{trigger:'axis',valueFormatter:display},legend:{type:'scroll',top:0},grid:{left:85,right:25,top:40,bottom:55},xAxis:{type:'category',data:rows.map(r=>r[x]),boundaryGap:bar},yAxis:{type:'value',scale:true,axisLabel:{formatter:display},splitLine:{lineStyle:{color:'#eef0f3'}}},dataZoom:[{type:'inside'},{type:'slider',bottom:8,height:18}],series:lines.map(([k,name])=>({name,type:bar?'bar':'line',showSymbol:seasonal||rows.length<20,symbolSize:seasonal?3:4,connectNulls:false,data:rows.map(r=>r[k]==null?null:r[k]*(percent?100:1))}))});},[rows,lines,x,percent,integer,seasonal,bar]);return <ChartFrame><div className="chart" ref={ref} role="img" aria-label={lines.map(l=>l[1]).join('、')}/></ChartFrame>;}
export function FundamentalAnalysis({product,end,revision}){
 const [catalog,C]=useState(null),[id,I]=usePreference('futures.fundamental.'+product+'.id',''),[result,D]=useState(null),[error,E]=useState(''),[all,A]=usePreference('futures.fundamental.all',false),[years,Y]=usePreference('futures.fundamental.years',false);
 const query=new URLSearchParams({...(!all?{product}:{}),...(end?{end}:{})}).toString();
 useEffect(()=>{const c=new AbortController();C(null);D(null);E('');api('/api/fundamentals/analysis?'+query,{signal:c.signal}).then(d=>{C(d);I(old=>d.indicators.some(i=>i.id===old)?old:d.indicators[0]?.id||'');}).catch(e=>{if(e.name!=='AbortError')E(e.message);});return()=>c.abort();},[query,revision]);
 useEffect(()=>{const c=new AbortController();D(null);if(!catalog||!id)return()=>c.abort();E('');api('/api/fundamentals/analysis?'+query+'&indicator='+encodeURIComponent(id),{signal:c.signal}).then(d=>{const known=catalog.indicators.find(i=>i.id===id);if(known?.snapshot_sha256!==d.snapshot_sha256)throw Error('数据版本已变化，请刷新');D(d);}).catch(e=>{if(e.name!=='AbortError')E(e.message);});return()=>c.abort();},[catalog,id,query]);
 const item=catalog?.indicators.find(i=>i.id===id);
 return <section className="panel"><h2>基本面指标</h2>
 <label className="check"><input type="checkbox" checked={all} onChange={e=>A(e.target.checked)}/>查看全部指标</label>
 {error&&<p role="alert">{error}</p>}{!catalog&&!error&&<p role="status">读取指标…</p>}
 {catalog&&<>{catalog.failures.map(f=><p key={f.source} role="status">{f.source}数据暂不可用</p>)}

 {!catalog.indicators.length?<p className="empty">当前品种暂无基本面数据。</p>:<>
 <div className="filters"><label>指标<select value={id} onChange={e=>{D(null);I(e.target.value);}}>{catalog.indicators.map(i=><option key={i.id} value={i.id}>{i.provider} · {i.name} · {i.code}</option>)}</select></label></div>
 {item&&<><p>{item.provider} · {{D:'日',W:'周',M:'月'}[item.frequency]||item.frequency} · {item.unit}</p><p className="notice">{item.usage==='technical_candidate_only'?'试验指标 · 尚未核验':'研究参考'}</p></>}
 {!result&&!error?<p role="status">读取指标序列…</p>:result&&<>{!result.latest?<p>截止日期之前无观测。</p>:<>
 <div className="metrics"><Metric label={'最新值 / '+item.unit} value={fmt(result.latest.raw_value)} hint={'最近观测：'+result.latest.observation_date}/><Metric label="前次变化" value={fmt(result.change)} hint={'最新值 − 前值（'+(result.previous?.observation_date||'无')+'）'}/><Metric label="历史分位" value={result.historical_percentile==null?'—':fmt(result.historical_percentile)+'%'} hint={'此前 '+result.history_n+' 个观测的分位（最多252个）；未季调。'}/><Metric label="观测距今 / 日" value={fmt(result.age_days)} hint="截止日期 − 最近观测日期（自然日）。"/></div>
 <Chart rows={result.points} x="observation_date" lines={[[ 'raw_value',item.name+' / '+item.unit ]]}/>
 <h3>历年季节性 / {item.unit}</h3><label className="check"><input type="checkbox" checked={years} onChange={e=>Y(e.target.checked)}/>显示全部年份（默认最近5年）</label><HistoryChart result={{...result.seasonal,unit:item.unit,accessible_label:'基本面历年季节性对比图'}} showAllYears={years}/>
 </>}
 <details><summary>历史观测</summary><div className="table-wrap"><table><thead><tr><th>观测期</th><th className="num">原始值 / {item.unit}</th></tr></thead><tbody>{result.points.map(p=><tr key={p.observation_date}><td>{p.observation_date}</td><td className="num">{fmt(p.raw_value)}</td></tr>)}</tbody></table></div></details></>}
 </>}</>}
 </section>;
}

function PairPosition({data,start,end,revision}){
 const {product,near,far}=data,[positions,setPositions]=useState(null),[error,setError]=useState('');
 useEffect(()=>{const c=new AbortController();setPositions(null);setError('');if(!near||!far)return()=>c.abort();Promise.all([near,far].map(symbol=>api('/api/contract-position?'+new URLSearchParams({product,symbol,...(start?{start}:{}),...(end?{end}:{})}),{signal:c.signal}))).then(setPositions).catch(e=>{if(e.name!=='AbortError')setError(e.message);});return()=>c.abort();},[product,near,far,start,end,revision]);
 const rows=useMemo(()=>{const days=new Map();positions?.forEach((leg,i)=>leg.series.forEach(p=>{if(!days.has(p.trade_date))days.set(p.trade_date,{trade_date:p.trade_date});Object.assign(days.get(p.trade_date),{['position'+i]:p.position,['change'+i]:p.change});}));return [...days.values()].sort((a,b)=>a.trade_date.localeCompare(b.trade_date));},[positions]);
 if(!near||!far)return <p className="empty">当前窗口不足两个合约</p>;
 return <><section className="panel"><h2>合约持仓 / 手</h2>{error?<p role="alert">{error}</p>:!positions?<p role="status">读取合约持仓…</p>:<><div className="metrics">{positions.map((leg,i)=><Metric key={i} label={[near,far][i]} value={fmt(leg.latest?.position)} hint={'持仓 / 手；观测日 '+(leg.latest?.trade_date||'无')+'；较前次变化 '+fmt(leg.latest?.change)+' 手'}/>)}</div><Chart rows={rows} lines={[["position0",near],["position1",far]]} integer/></>}</section>
 <div className="grid"><section className="panel"><h2>持仓增减 / 手</h2>{positions&&<Chart rows={rows} lines={[["change0",near],["change1",far]]} integer bar/>}</section><section className="panel"><h2>远月占比 / %</h2><Chart rows={data.rollover.series} lines={[["new_oi_share","持仓占比"],["new_volume_share","成交占比"]]} percent/></section></div>
 <details className="panel"><summary>两腿价格 · {near} / {far}</summary><Chart rows={data.spread.series} lines={[["near_price",near],["far_price",far]]}/></details></>;
}
function SeasonalPosition({product,end,revision}){
 const [result,setResult]=useState(null),[error,setError]=useState(''),[showAll,setShowAll]=usePreference('futures.seasonal.showAll',false);
 useEffect(()=>{const c=new AbortController();setResult(null);setError('');api('/api/seasonal-position?'+new URLSearchParams({product,end,all_years:true}),{signal:c.signal}).then(setResult).catch(e=>{if(e.name!=='AbortError')setError(e.message);});return()=>c.abort();},[product,end,revision]);
 return <section className="panel history-panel"><div className="filters history-toolbar"><h2 className="section-label">总持仓</h2><label>显示范围<select value={showAll?'all':'recent'} onChange={e=>setShowAll(e.target.value==='all')}><option value="recent">最近5个有效年份</option><option value="all">全部已有年份</option></select></label>{result&&<span className="sub">{result.years.length} 年 · 持仓 / 手{result.meta.stale?' · 缓存降级':''}</span>}</div>{error?<p role="alert">{error}</p>:result?<><HistoryChart result={{...result, axis:result.series.map(r=>r.month_day), mode:"seasonal", metric:"position"}} showAllYears={showAll}/></>:<p role="status">正在读取全部历史持仓…</p>}</section>;
}
function SpreadStatistics({risk,near,far,seasonal}) {
 if (!risk) return null;
 return <section className="spread-statistics" aria-label="价差统计">
  <div className="filters"><h2>价差统计</h2><span className="sub">{near} − {far} · 与价差同单位</span></div>
  <div className="spread-stat-grid">
  {seasonal&&<div><div className="stats-heading"><h3>历年同期</h3><span>{seasonal.n} 个历史年份</span></div>
   {seasonal.current?.lag_days>0&&<p className="sub">当前观测 {seasonal.current.trade_date}（距截止日 {seasonal.current.lag_days} 天）</p>}
   <table className="seasonal-stats"><tbody>{[['同期中位数',seasonal.median,'同期历史价差中位数'],['偏离中位数',seasonal.current&&seasonal.median!=null?seasonal.current.value-seasonal.median:null,'当前价差 − 同期历史中位数'],['同期最低',seasonal.min,'同期历史价差最小值'],['同期最高',seasonal.max,'同期历史价差最大值']].map(([label,value,hint])=><tr key={label}><th><Metric label={label} hint={hint+'；同月日向前7日匹配，每年一个样本，不含当前交割年。'}/></th><td className="num">{fmt(value)}</td></tr>)}</tbody></table>
  </div>}
  <div><div className="stats-heading"><h3>当前组合波动</h3><span>{risk.start||'—'} 至 {risk.as_of||'—'}</span></div>
  <div className="metrics">{risk.volatility.map(v=><Metric key={v.window} label={v.window+'日标准差'} value={fmt(v.value)} hint={'最近'+v.window+'次价差变动的样本标准差，非年化、非百分比；有效 '+v.n+'/'+v.window+(v.value==null?'，窗口不足或有缺口':'')}/>)}</div>
  <div className="table-wrap"><table><thead><tr><th><Metric label="观察窗口" hint="以每个历史观测日为起点，比较随后5／20个观测日的收盘价差；统计范围最多253个品种观测日。"/></th><th className="num"><Metric label="最大上涨" hint="每个窗口内价差相对起点的最大上涨，再取所有完整窗口中的最大值。"/></th><th className="num"><Metric label="最大下跌" hint="每个窗口内价差相对起点的最大下跌，再取所有完整窗口中的最大幅度；不是峰谷回撤。"/></th><th className="num"><Metric label="有效窗口" hint="窗口允许重叠，有缺口的窗口剔除；不是独立交易次数。"/></th></tr></thead><tbody>{risk.horizons.map(h=><tr key={h.horizon}><td>{h.horizon}日内</td>{['sell','buy'].map((direction,i)=>{const point=h[direction],value=point?.value;return <td className={'num '+(value>0?(i?'direction-short':'direction-long'):'')} key={direction}><Metric label={value==null?'—':(value>0?(i?'−':'+'):'')+fmt(value)} hint={point?'起点 '+point.entry_date+' → 极值日 '+point.adverse_date+'；窗口截至 '+point.end_date:'无完整窗口'}/></td>;})}<td className="num"><Metric label={String(h.n)} hint={'剔除缺口窗口 '+h.skipped+' 个；0表示未出现该方向变动，—表示无有效窗口。'}/>{h.n<20&&<small>样本少</small>}</td></tr>)}</tbody></table></div>
 </div></div></section>;
}
const tabs=['持仓季节性','跨期分析','全市场雷达','机会扫描','期限结构','持仓换月','历史合约','基差分析','基本面分析'];
const tabLabel=t=>({'持仓季节性':'季节持仓','全市场雷达':'市场雷达','机会扫描':'组合筛选','跨期分析':'跨期价差','基差分析':'基差','基本面分析':'基本面'})[t]||t;
export function App(){
 const [spreadMode,setSpreadMode]=useState(()=>{const p=new URLSearchParams(location.search);return p.get('spread_mode')==='current'||!p.has('spread_mode')&&p.get('spread_view')==='history'?'current':'annual';});
 const [tab,T]=useState(()=>{const p=new URLSearchParams(location.search),t=p.get('tab');return t==='持仓季节性'&&p.get('season_view')==='spread'?'跨期分析':tabs.includes(t)?t:tabs[0];}),[q,Q]=useState(()=>({product:'IM',...Object.fromEntries([...new URLSearchParams(location.search)].filter(([k])=>['product','near','far','start','end','focus_symbol'].includes(k)))})),[products,P]=useState([]),[response,D]=useState(null),[err,E]=useState(''),[busy,B]=useState(false),[rev,R]=useState(0),[note,N]=useState('');
 useEffect(()=>{const url=new URL(location.href);for(const k of ['product','near','far','start','end','focus_symbol']){if(q[k])url.searchParams.set(k,q[k]);else url.searchParams.delete(k);}url.searchParams.set('tab',tab);if(tab==='跨期分析')url.searchParams.set('spread_view','history');else url.searchParams.delete('spread_view');if(tab==='跨期分析')url.searchParams.set('spread_mode',spreadMode);else url.searchParams.delete('spread_mode');url.searchParams.delete('season_view');url.searchParams.delete('price_type');history.replaceState(null,'',url);},[tab,q,spreadMode]);
 const pairPage=tab==='跨期分析'||tab==='持仓换月',dateWindow=!(tab==='跨期分析'&&spreadMode==='annual')&&!(['期限结构','基本面分析','历史合约','持仓季节性'].includes(tab));
 const qs=new URLSearchParams({...Object.fromEntries(Object.entries(q).filter(([k,v])=>v&&k!=='focus_symbol'&&(dateWindow||k!=='start')&&(pairPage||!['near','far'].includes(k)))),view:({'跨期分析':'spread','期限结构':'curve','持仓换月':'rollover','机会扫描':'scan'})[tab]||'catalog'}).toString();
 const d=response?.query===qs?response.data:null;
 useEffect(()=>{api('/api/products').then(x=>P(x.products)).catch(e=>E(e.message));},[rev]);
 useEffect(()=>{if(['全市场雷达','基差分析','基本面分析'].includes(tab)){B(false);D(null);E('');return;}const c=new AbortController();B(true);E('');D(null);api('/api/dashboard?'+qs,{signal:c.signal}).then(data=>D({query:qs,data})).catch(e=>{if(e.name!=='AbortError')E(e.message);}).finally(()=>{if(!c.signal.aborted)B(false);});return()=>c.abort();},[qs,rev,tab]);
 useEffect(()=>{if(tab!=='持仓换月'||!q.focus_symbol||!d)return;const focus=q.focus_symbol,other=d.near===focus?d.far:d.near;if(d.contracts.some(c=>c.symbol===focus)&&other){const [near,far]=[focus,other].sort();Q(p=>({...p,near,far,focus_symbol:undefined}));}else{N('原合约不在当前日期目录，已选择当前活跃组合');Q(p=>({...p,focus_symbol:undefined}));}},[tab,q.focus_symbol,d]);
 const latest=d?.spread?.summary.latest||{};
 function pair(near,far){Q({...q,near,far});setSpreadMode('annual');T('跨期分析');}

 function chooseContract(k,value){if(!value){Q({...q,near:undefined,far:undefined,focus_symbol:undefined});return;}const pair=[k==='near'?value:q.near||d?.near,k==='far'?value:q.far||d?.far].sort();Q({...q,near:pair[0],far:pair[1],focus_symbol:undefined});}

 const spreadModeControl=<label>图表<select value={spreadMode} onChange={e=>setSpreadMode(e.target.value)}><option value="annual">历年对照</option><option value="current">当前组合</option></select></label>;
 const spreadQuote=<div className="spread-quote">{[['跨期价差',fmt(latest.spread),'近月 − 远月'],['前次变动',fmt(latest.change),'本次 − 前次有效价差，可能跨日']].map(([label,value,hint])=><Metric key={label} label={label} value={value} hint={hint+'；配对日 '+(latest.trade_date||'无')}/>)}</div>;
 const filters=metricControl=><>{tab==='跨期分析'&&d?.near&&d?.far&&spreadQuote}<select aria-label="品种" className="product-select" value={q.product} onChange={e=>Q({product:e.target.value,...(tab==='基本面分析'?{end:q.end}:{})})}>{!products.length&&<option>IM</option>}{products.map(p=><option key={p.product} value={p.product}>{p.product}{p.name?'/'+p.name:''}</option>)}</select>{metricControl}{pairPage&&['near','far'].map((k,i)=><label key={k}>{i?'远月':'近月'}<select value={q[k]||d?.[k]||''} onChange={e=>chooseContract(k,e.target.value)}><option value="">自动选取</option>{d?.contracts.filter(c=>c.symbol!==(k==='near'?(q.far||d.far):(q.near||d.near))).map(c=><option key={c.symbol}>{c.symbol}</option>)}</select></label>)}{(dateWindow?['start','end']:['end']).map((k,i)=><label key={k}>{k==='end'?'截止日期':'开始日期'}<input type="date" value={q[k]||(k==='end'?d?.meta.as_of:'')||''} onChange={e=>Q({...q,[k]:e.target.value})}/></label>)}</>,dataInfo=d&&(q.end&&q.end!==d.meta.as_of||pairPage&&latest.trade_date!==d.meta.as_of||d.meta.stale)&&<span className="status"><span>{q.end&&q.end!==d.meta.as_of&&<>数据日期 {d.meta.as_of}</>}{pairPage&&latest.trade_date!==d.meta.as_of&&<> · 配对 {latest.trade_date||'无'}</>}</span><span>{d.meta.stale?'缓存降级':''}</span></span>;

 return <div className="shell"><main><nav className="section-nav" aria-label="期货分析">{tabs.map(t=><button key={t} aria-pressed={tab===t} className={tab===t?'active':''} onClick={()=>T(t)}>{tabLabel(t)}</button>)}{tab!=='全市场雷达'&&<button className="refresh" disabled={busy} onClick={()=>R(rev+1)}>刷新</button>}</nav><h1 className="sr-only">{tabLabel(tab)}</h1>
 {(!['全市场雷达','持仓季节性','历史合约'].includes(tab)||!d&&['持仓季节性','历史合约'].includes(tab))&&!(tab==='跨期分析'&&d?.near&&d?.far)&&<section className="filters page-filters">{filters()}{dataInfo}</section>}
 {tab==='全市场雷达'&&<RadarPanel onSelect={(product,event,result,target)=>{if(target==='跨期分析'){setSpreadMode('annual');}Q({product,start:result.start,end:result.as_of,...(event?.near&&!event.far?{focus_symbol:event.near}:{}),...(event?.far?{near:event.near,far:event.far}:{})});T(target);}}/>}
 {tab==='基本面分析'&&<FundamentalAnalysis key={q.product} product={q.product} end={q.end} revision={rev}/>}
 {tab==='基差分析'&&<BasisPanel key={q.product} product={q.product} start={q.start} end={q.end} priceType="close" revision={rev} Chart={Chart} onProduct={product=>Q({product})}/>}
 {err&&<div className="error" role="alert">读取失败：{err} <button onClick={()=>Q({product:'IM'})}>重置筛选</button></div>}{busy&&<div className="empty" role="status">读取行情…</div>}
{d&&<>
 {tab==='机会扫描'&&<ScanPanel data={d} onPair={pair}/>}
 {tab==='跨期分析'&&d.near&&d.far&&<>
 {spreadMode==='annual'?<HistoryPanel controls={yearsControl=> <>{filters(yearsControl)}{spreadModeControl}</>} info={dataInfo} key={[d.product,d.near,d.far].join(':')} product={d.product} end={d.meta.as_of} near={d.near} far={d.far} priceType={d.price_type} revision={rev} view="spread">{seasonal=><SpreadStatistics risk={d.risk} near={d.near} far={d.far} seasonal={seasonal}/>}</HistoryPanel>:<><SpreadPanel controls={<>{filters()}{spreadModeControl}{dataInfo}</>} key={`${d.product}-${d.near}-${d.far}-${q.start||''}`} data={d} start={q.start} revision={rev}/><SpreadStatistics risk={d.risk} near={d.near} far={d.far}/></>}</>}
 {tab==='期限结构'&&<CurvePanel key={d.product+d.meta.as_of} Chart={Chart} data={d} onPair={pair}/>}
 {tab==='持仓换月'&&<PairPosition data={d} start={q.start} end={q.end||d.meta.as_of} revision={rev}/>}
 {tab==='持仓季节性'&&d.meta.as_of&&<><HistoryPanel title="同月持仓" controls={filters} info={dataInfo} key={d.product} product={d.product} end={d.meta.as_of} near={d.near} far={d.far} priceType={d.price_type} revision={rev} view="month"/><SeasonalPosition product={d.product} end={d.meta.as_of} revision={rev}/></>}
 {tab==='历史合约'&&d.meta.as_of&&<HistoryPanel controls={filters} info={dataInfo} key={d.product} product={d.product} end={d.meta.as_of} near={d.near} far={d.far} priceType={d.price_type} revision={rev}/>}
</>}
 {note&&<div className="toast" role="status">{note}<button onClick={()=>N('')}>关闭</button></div>}</main></div>;
}
