import { selectedProduct, rememberProduct } from '../../../frontend/src/preferences.js';
import { conditionalFetch as fetch } from '../../../frontend/src/transport.js';
import { useChart, ChartFrame, Metric, usePreference, observedRows, useResource, RefreshButton } from '../../../frontend/src/charts.js';
import {useEffect,useMemo,useRef,useState} from 'react';
import {ScanPanel,CurvePanel,RadarPanel,BasisPanel,ObservationDate,SpreadMatrix,SeasonalSpreadChart,RolloverProgress} from './MarketPanels.jsx';
import SpreadPanel from './SpreadPanel.jsx';
import HistoryPanel, {HistoryChart, InventoryPanel} from './HistoryPanel.jsx';
const fmt=v=>v==null?'—':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:2});
async function api(url,options){const r=await fetch(url,options),d=await r.json();if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:JSON.stringify(d.detail));return d;}
export function Chart({rows,lines,x='trade_date',percent=false,integer=false,seasonal=false,bar=false,selected=[],tooltip,zoomGroup,syncLegend=false}){const ref=useRef();useChart(ref,c=>{const display=v=>v==null?'—':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:integer?0:2})+(percent?'%':'');c.setOption({color:['#4e7fb9','#568f84','#a48b59','#8c7b9f','#b36c73'],tooltip:{trigger:'axis',...(tooltip?{formatter:tooltip}:{valueFormatter:display})},legend:{type:'scroll',top:0},grid:{left:85,right:25,top:40,bottom:55},xAxis:{type:'category',data:rows.map(r=>r[x]),boundaryGap:bar},yAxis:{type:'value',scale:true,axisLabel:{formatter:display},splitLine:{lineStyle:{color:'#eef0f3'}}},dataZoom:[{type:'inside'},{type:'slider',bottom:8,height:18}],series:lines.map(([k,name],i)=>({name,...(i===0&&selected.length?{markPoint:{symbol:'circle',symbolSize:8,data:selected.flatMap((symbol,j)=>{const index=rows.findIndex(r=>r[x]===symbol&&r[k]!=null);return index<0?[]:[{coord:[index,rows[index][k]],label:{formatter:j?'远月':'近月',position:j?'bottom':'top'}}];})}}:{}),type:bar?'bar':'line',showSymbol:seasonal||rows.length<20,symbolSize:seasonal?3:4,connectNulls:false,data:rows.map(r=>r[k]==null?null:r[k]*(percent?100:1))}))}, true);},[rows,lines,x,percent,integer,seasonal,bar,selected,tooltip],zoomGroup,syncLegend);return <ChartFrame><div className="chart" ref={ref} role="img" aria-label={lines.map(l=>l[1]).join('、')}/></ChartFrame>;}
function PairPosition({data}){
 const current=data.rollover_progress?.rounds[0],near=current?.outgoing,far=current?.incoming;
 const zoomGroup=useMemo(()=>new Set(),[data]);
 const rows=useMemo(()=>{const shares=new Map(data.rollover.series.map(p=>[p.trade_date,p]));return observedRows((current?.series||[]).map((p,i,points)=>{
  const previous=points[i-1],total=p.outgoing+p.incoming;
  return {...shares.get(p.trade_date),trade_date:p.trade_date,
   near_change:p.outgoing!=null&&previous?.outgoing!=null?p.outgoing-previous.outgoing:null,
   far_change:p.incoming!=null&&previous?.incoming!=null?p.incoming-previous.incoming:null,
   new_oi_share:p.outgoing!=null&&p.incoming!=null&&total>0?p.incoming/total:null};
 }),['near_change','far_change','new_oi_share','new_volume_share']);},[data,current]);
 return <><RolloverProgress data={data.rollover_progress}/>{rows.length>0&&<div className="grid"><section className="panel"><h2>持仓增减 / 手</h2><Chart zoomGroup={zoomGroup} rows={rows} lines={[["near_change",near],["far_change",far]]} integer bar/></section><section className="panel"><h2>远月占比 / %</h2><Chart zoomGroup={zoomGroup} rows={rows} lines={[["new_oi_share","远月持仓占比"],["new_volume_share","远月成交占比"]]} percent/></section></div>}</>;
}
function SeasonalPosition({product,end,revision}){
 const [showAll,setShowAll]=usePreference('futures.seasonal.showAll',false);
 const {data:result,error,busy}=useResource('/api/seasonal-position?'+new URLSearchParams({product,end,all_years:true}),revision);
 return <section className="panel history-panel" aria-busy={busy}><div className="filters history-toolbar"><h2 className="section-label">总持仓</h2><label>显示范围<select value={showAll?'all':'recent'} onChange={e=>setShowAll(e.target.value==='all')}><option value="recent">近5个有效年</option><option value="all">全部已有年份</option></select></label>{result&&<span className="sub">{result.years.length} 年 · 持仓 / 手{result.meta.stale?' · 缓存降级':''}</span>}</div>{error?<p role="alert">{error}</p>:result?<><HistoryChart result={{...result, axis:result.series.map(r=>r.month_day), mode:"seasonal", metric:"position"}} showAllYears={showAll}/></>:<p role="status">正在读取全部历史持仓…</p>}</section>;
}
function SpreadStatistics({risk,near,far,seasonal}) {
 if (!risk) return null;
 return <section className="spread-statistics" aria-label="价差统计">
  <div className="filters"><h2>价差统计</h2><span className="sub">{near} − {far}</span></div>
  <div className="spread-stat-grid">
  {seasonal&&<div><div className="stats-heading"><h3>历年同期</h3><span>{seasonal.n} 个历史年份</span></div>
   {seasonal.current?.lag_days>0&&<p className="sub">当前观测 {seasonal.current.trade_date}（距截止日 {seasonal.current.lag_days} 天）</p>}
   <SeasonalSpreadChart summary={seasonal}/>
  </div>}
  <div><div className="stats-heading"><h3>当前组合波动</h3><span>{risk.start||'—'} 至 {risk.as_of||'—'}</span></div>
  <div className="metrics">{risk.volatility.map(v=><Metric key={v.window} label={v.window+'日标准差'} value={fmt(v.value)} hint={'最近'+v.window+'次价差变动的样本标准差，非年化、非百分比；有效 '+v.n+'/'+v.window+(v.value==null?'，窗口不足或有缺口':'')}/>)}</div>
  <div className="table-wrap"><table><thead><tr><th><Metric label="观察窗口" hint="以每个历史观测日为起点，比较随后5／20个观测日的收盘价差；统计范围最多253个品种观测日。"/></th><th className="num"><Metric label="最大上涨" hint="每个窗口内价差相对起点的最大上涨，再取所有完整窗口中的最大值。"/></th><th className="num"><Metric label="最大下跌" hint="每个窗口内价差相对起点的最大下跌，再取所有完整窗口中的最大幅度；不是峰谷回撤。"/></th><th className="num"><Metric label="有效窗口" hint="窗口允许重叠，有缺口的窗口剔除；不是独立交易次数。"/></th></tr></thead><tbody>{risk.horizons.map(h=><tr key={h.horizon}><td>{h.horizon}日内</td>{['sell','buy'].map((direction,i)=>{const point=h[direction],value=point?.value;return <td className={'num '+(value>0?(i?'direction-short':'direction-long'):'')} key={direction}><Metric label={value==null?'—':(value>0?(i?'−':'+'):'')+fmt(value)} hint={point?'起点 '+point.entry_date+' → 极值日 '+point.adverse_date+'；窗口截至 '+point.end_date:'无完整窗口'}/></td>;})}<td className="num"><Metric label={String(h.n)} hint={'剔除缺口窗口 '+h.skipped+' 个；0表示未出现该方向变动，—表示无有效窗口。'}/>{h.n<20&&<small>样本少</small>}</td></tr>)}</tbody></table></div>
 </div></div></section>;
}
const tabs=['持仓季节性','跨期分析','库存仓单','全市场雷达','机会扫描','持仓换月','历史合约','基差分析'];
const tabLabel=t=>({'持仓季节性':'季节持仓','全市场雷达':'市场雷达','机会扫描':'价差筛选','持仓换月':'移仓进度','历史合约':'合约走势','跨期分析':'跨期价差','基差分析':'基差'})[t]||t;
export function App(){
 const [spreadMode,setSpreadMode]=useState(()=>{const p=new URLSearchParams(location.search);return p.get('tab')==='期限结构'||p.get('spread_mode')==='curve'?'curve':p.get('spread_mode')==='current'||!p.has('spread_mode')&&p.get('spread_view')==='history'?'current':'annual';});
 const [tab,T]=useState(()=>{const p=new URLSearchParams(location.search),t=p.get('tab');return t==='期限结构'||t==='持仓季节性'&&p.get('season_view')==='spread'?'跨期分析':tabs.includes(t)?t:tabs[0];}),[q,Q]=useState(()=>({product:selectedProduct('IM'),...Object.fromEntries([...new URLSearchParams(location.search)].filter(([k])=>['product','near','far','end','focus_symbol'].includes(k)))})),[products,P]=useState([]),[productError,E]=useState(''),[rev,R]=useState(0),[note,N]=useState('');
 useEffect(()=>{const url=new URL(location.href);for(const k of ['product','near','far','end','focus_symbol']){if(q[k]&&!(tab==='持仓换月'&&['near','far','focus_symbol'].includes(k)))url.searchParams.set(k,q[k]);else url.searchParams.delete(k);}url.searchParams.set('tab',tab);if(tab==='跨期分析')url.searchParams.set('spread_view','history');else url.searchParams.delete('spread_view');if(tab==='跨期分析')url.searchParams.set('spread_mode',spreadMode);else url.searchParams.delete('spread_mode');url.searchParams.delete('start');url.searchParams.delete('season_view');url.searchParams.delete('price_type');history.replaceState(null,'',url);},[tab,q,spreadMode]);
 useEffect(()=>rememberProduct(q.product),[q.product]);
 const pairPage=tab==='跨期分析';
 const qs=new URLSearchParams({...Object.fromEntries(Object.entries(q).filter(([k,v])=>v&&k!=='focus_symbol'&&(pairPage||!['near','far'].includes(k)))),view:({'跨期分析':spreadMode==='curve'?'curve':'spread','持仓换月':'rollover','机会扫描':'scan'})[tab]||'catalog'}).toString();
 const {data:d,error:loadError,busy}=useResource(['全市场雷达','基差分析','库存仓单'].includes(tab)?null:'/api/dashboard?'+qs,rev,tab),err=loadError||productError;
 const renderedMode=useRef(spreadMode);if(!busy)renderedMode.current=spreadMode;
 const displayMode=busy&&d?renderedMode.current:spreadMode;
 useEffect(()=>{api('/api/products').then(x=>{P(x.products);E('');}).catch(e=>E(e.message));},[rev]);

 const latest=d?.spread?.summary.latest||{};
 function pair(near,far){Q({...q,near,far});setSpreadMode('annual');T('跨期分析');}
 const matrix=d&&<SpreadMatrix data={d} onPair={(near,far)=>{Q({...q,near,far});if(spreadMode==='curve')setSpreadMode('annual');}}/>;

 function chooseContract(k,value){if(!value){Q({...q,near:undefined,far:undefined,focus_symbol:undefined});return;}const pair=[k==='near'?value:q.near||d?.near,k==='far'?value:q.far||d?.far].sort();Q({...q,near:pair[0],far:pair[1],focus_symbol:undefined});}

 const spreadModeControl=<select aria-label="图表" className="view-select" value={spreadMode} onChange={e=>setSpreadMode(e.target.value)}><option value="annual">价差季节性</option><option value="current">价差走势</option><option value="curve">期限曲线</option></select>;
 const spreadQuote=<div className="spread-quote">{[['跨期价差',latest.spread],['前日变动',latest.change]].map(([label,value])=><span className="research-metric" key={label}><span>{label}</span><strong>{fmt(value)}</strong></span>)}</div>;
 const pairControls=['near','far'].map((k,i)=><label key={k}>{i?'远月':'近月'}<select disabled={busy} value={q[k]||d?.[k]||''} onChange={e=>chooseContract(k,e.target.value)}><option value="">自动选取</option>{d?.contracts.filter(c=>c.symbol!==(k==='near'?(q.far||d.far):(q.near||d.near))).map(c=><option key={c.symbol}>{c.symbol}</option>)}</select></label>);
 const filters=(metricControl,latestDate,loading=false)=><>{tab==='跨期分析'&&spreadModeControl}<select aria-label="品种" className="product-select" value={q.product} onChange={e=>Q({product:e.target.value,end:q.end})}>{!products.length&&<option value={q.product}>{q.product}</option>}{products.map(p=><option key={p.product} value={p.product}>{p.product}{p.name?'/'+p.name:''}</option>)}</select>{tab!=='跨期分析'&&metricControl}{tab==='跨期分析'&&pairControls}<ObservationDate value={q.end||''} latest={latestDate||d?.meta.as_of} onChange={e=>Q({...q,end:e.target.value})}/>{tab==='跨期分析'&&metricControl}{tab==='跨期分析'&&spreadMode!=='curve'&&d?.near&&d?.far&&spreadQuote}<RefreshButton busy={busy||loading} onClick={()=>R(rev+1)}/></>,dataInfo=d?.meta.as_of&&(q.end&&q.end!==d.meta.as_of||d.spread&&pairPage&&latest.trade_date!==d.meta.as_of||d.meta.stale)&&<span className="status"><span>{q.end&&q.end!==d.meta.as_of&&<>数据日期 {d.meta.as_of}</>}{d.spread&&pairPage&&latest.trade_date!==d.meta.as_of&&<> · 配对 {latest.trade_date||'无'}</>}</span><span>{d.meta.stale?'缓存降级':''}</span></span>;

 return <div className="shell"><main className="board-content" aria-busy={busy}><nav className="board-tabs section-nav" aria-label="期货分析">{tabs.map(t=><button key={t} aria-pressed={tab===t} className={tab===t?'active':''} onClick={()=>T(t)}>{tabLabel(t)}</button>)}</nav><h1 className="sr-only">{tabLabel(tab)}</h1>
 {(!['全市场雷达','持仓季节性','历史合约','基差分析','库存仓单'].includes(tab)||!d?.meta.as_of&&['持仓季节性','历史合约'].includes(tab))&&!(tab==='跨期分析'&&d&&(displayMode==='curve'||d.near&&d.far))&&<section className="filters page-filters">{filters()}{dataInfo}</section>}
 {tab==='全市场雷达'&&<RadarPanel onSelect={(product,event,result,target)=>{if(target==='跨期分析'){setSpreadMode('annual');}Q({product,end:result.as_of,...(event?.near&&!event.far?{focus_symbol:event.near}:{}),...(event?.far?{near:event.near,far:event.far}:{})});T(target);}}/>}
 {tab==='基差分析'&&<BasisPanel product={q.product} end={q.end} priceType="close" revision={rev} Chart={Chart} controls={filters} onProduct={product=>Q({product,end:q.end})}/>}
 {tab==='库存仓单'&&<InventoryPanel product={q.product} end={q.end} revision={rev} controls={filters}/>}
 {err&&<div className="error" role="alert">读取失败：{err} <button onClick={()=>Q({product:'IM'})}>重置筛选</button></div>}{busy&&!d&&<div className="empty" role="status">读取行情…</div>}
{d&&<>
 {!d.meta.as_of&&['持仓季节性','历史合约'].includes(tab)&&<p className="empty">所选日期前暂无该品种数据</p>}
 {tab==='机会扫描'&&<ScanPanel data={d} onPair={pair}/>}
 {tab==='跨期分析'&&(displayMode==='curve'?<CurvePanel Chart={Chart} data={d} onPair={pair} beforeChart={matrix} controls={<>{filters()}{dataInfo}</>}/>:d.near&&d.far?<>
 {displayMode==='annual'?<HistoryPanel controls={filters} info={dataInfo} beforeChart={matrix} product={d.product} end={d.meta.as_of} near={d.near} far={d.far} priceType={d.price_type} revision={rev} view="spread">{seasonal=><SpreadStatistics risk={d.risk} near={d.near} far={d.far} seasonal={seasonal}/>}</HistoryPanel>:<><SpreadPanel controls={(spanControl,loading)=><>{filters(spanControl,undefined,loading)}{dataInfo}</>} beforeChart={matrix} data={d} revision={rev}/><SpreadStatistics risk={d.risk} near={d.near} far={d.far}/></>}</>:<>{matrix}<p className="empty">当前窗口不足两个合约</p></>)}
 {tab==='持仓换月'&&<PairPosition data={d}/>}
 {tab==='持仓季节性'&&d.meta.as_of&&<><HistoryPanel title="同月持仓" controls={filters} info={dataInfo} product={d.product} end={d.meta.as_of} near={d.near} far={d.far} priceType={d.price_type} revision={rev} view="month"/><SeasonalPosition product={d.product} end={d.meta.as_of} revision={rev}/></>}
 {tab==='历史合约'&&d.meta.as_of&&<HistoryPanel controls={filters} info={dataInfo} product={d.product} end={d.meta.as_of} near={d.near} far={d.far} priceType={d.price_type} revision={rev}/>}
</>}
 {note&&<div className="toast" role="status">{note}<button onClick={()=>N('')}>关闭</button></div>}</main></div>;
}
