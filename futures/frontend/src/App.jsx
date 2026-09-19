import {useEffect,useRef,useState} from 'react';
import {ScanPanel,CurvePanel,RadarPanel,BasisPanel,ResearchCard,FundamentalUpdateStatus,FundamentalCandidates} from './MarketPanels.jsx';
import SpreadPanel from './SpreadPanel.jsx';
import HistoryPanel, {HistoryChart} from './HistoryPanel.jsx';
import * as ec from 'echarts/core';
import {LineChart,BarChart} from 'echarts/charts';
import {GridComponent,TooltipComponent,DataZoomComponent,LegendComponent} from 'echarts/components';
import {CanvasRenderer} from 'echarts/renderers';
ec.use([LineChart,BarChart,GridComponent,TooltipComponent,DataZoomComponent,LegendComponent,CanvasRenderer]);
const fmt=v=>v==null?'—':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:2});
async function api(url,options){const r=await fetch(url,options),d=await r.json();if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:JSON.stringify(d.detail));return d;}
export function Chart({rows,lines,x='trade_date',percent=false,integer=false,seasonal=false,bar=false}){const ref=useRef();useEffect(()=>{const c=ec.init(ref.current,{textStyle:{fontFamily:getComputedStyle(ref.current).fontFamily},tooltip:{textStyle:{fontFamily:getComputedStyle(ref.current).fontFamily}}});const display=v=>v==null?'—':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:integer?0:2})+(percent?'%':'');c.setOption({color:['#2563eb','#0d9488','#d97706','#9333ea','#e11d48'],tooltip:{trigger:'axis',valueFormatter:display},legend:{bottom:0},grid:{left:85,right:25,top:25,bottom:75},xAxis:{type:'category',data:rows.map(r=>r[x]),boundaryGap:bar},yAxis:{type:'value',scale:true,axisLabel:{formatter:display},splitLine:{lineStyle:{color:'#edf1f6'}}},dataZoom:[{type:'inside'},{type:'slider',bottom:27,height:18}],series:lines.map(([k,name])=>({name,type:bar?'bar':'line',showSymbol:seasonal||rows.length<20,symbolSize:seasonal?3:4,connectNulls:false,data:rows.map(r=>r[k]==null?null:r[k]*(percent?100:1))}))});const o=new ResizeObserver(()=>c.resize());o.observe(ref.current);return()=>{o.disconnect();c.dispose();};},[rows,lines,x,percent,integer,seasonal,bar]);return <div className="chart" ref={ref} role="img" aria-label={lines.map(l=>l[1]).join('、')}/>;}
export function FundamentalAnalysis({product,end,revision}){
 const [catalog,C]=useState(null),[id,I]=useState(''),[result,D]=useState(null),[error,E]=useState(''),[all,A]=useState(false),[years,Y]=useState(false);
 const query=new URLSearchParams({...(!all?{product}:{}),...(end?{end}:{})}).toString();
 useEffect(()=>{const c=new AbortController();C(null);D(null);E('');api('/api/fundamentals/analysis?'+query,{signal:c.signal}).then(d=>{C(d);I(old=>d.indicators.some(i=>i.id===old)?old:d.indicators[0]?.id||'');}).catch(e=>{if(e.name!=='AbortError')E(e.message);});return()=>c.abort();},[query,revision]);
 useEffect(()=>{const c=new AbortController();D(null);if(!catalog||!id)return()=>c.abort();E('');api('/api/fundamentals/analysis?'+query+'&indicator='+encodeURIComponent(id),{signal:c.signal}).then(d=>{const known=catalog.indicators.find(i=>i.id===id);if(known?.snapshot_sha256!==d.snapshot_sha256)throw Error('数据版本已变化，请刷新');D(d);}).catch(e=>{if(e.name!=='AbortError')E(e.message);});return()=>c.abort();},[catalog,id,query]);
 const item=catalog?.indicators.find(i=>i.id===id);
 return <section className="panel"><h2>基本面 · 趋势与季节性</h2><p className="sub">当前保存版本，按观测期截止筛选；不是历史当时已知数据。不参与交割基差或机会排名。</p>
 <label className="check"><input type="checkbox" checked={all} onChange={e=>A(e.target.checked)}/>查看全部已保存指标（含未关联品种）</label>
 {error&&<p role="alert">{error}</p>}{!catalog&&!error&&<p role="status">校验本地基本面快照…</p>}
 {catalog&&<>{catalog.failures.map(f=><p key={f.source} role="status">{f.source}：{f.reason}；其他来源不受影响</p>)}
 <h3>当前品种 · 基本面覆盖与可用性</h3><p className="sub">{catalog.coverage_note}</p>
 <div className="table-wrap coverage-table"><table><thead><tr><th>需求类别</th><th>使用状态</th><th>指标 / 最新保存观测</th><th>更新证据</th></tr></thead><tbody>{(catalog.coverage||[]).map(c=><tr key={c.category}><td>{c.label}</td><td>{c.status}</td><td>{c.indicators.length?c.indicators.map(i=><div key={i.id}>{i.name||i.id}<small>{i.last_observation||'无观测'} · {i.frequency||'频率未知'} / {i.unit||'单位未知'} · {i.status||c.status}</small></div>):'未接入已核验数据'}</td><td>{c.indicators.map(i=><div key={i.id}><small>取数：{i.last_fetch_at||'时间未核验'}</small><small>发布规则：{i.release_rule||'未核验，不自动判过期'}</small></div>)}</td></tr>)}</tbody></table></div>
 {!catalog.indicators.length?<p className="empty">{catalog.missing_reason}。可勾选“查看全部”浏览已有数据。</p>:<>
 <div className="filters"><label>指标<select value={id} onChange={e=>{D(null);I(e.target.value);}}>{catalog.indicators.map(i=><option key={i.id} value={i.id}>{i.provider} · {i.name} · {i.code}</option>)}</select></label></div>
 {item&&<><p>{item.provider} · {item.code} · {{D:'日',W:'周',M:'月'}[item.frequency]||item.frequency} · {item.unit} · {item.source}</p><p className="notice">{item.usage==='technical_candidate_only'?'CEIC技术候选，未业务准入':'Wind限定用途审阅'}：{item.limits.join('；')}</p></>}
 {!result&&!error?<p role="status">读取指标序列…</p>:result&&<>{!result.latest?<p>截止日期之前无观测。</p>:<>
 <div className="metrics"><article><p>最新保存值 / {item.unit}</p><strong>{fmt(result.latest.raw_value)}</strong><small>观测期 {result.latest.observation_date}</small></article><article><p>较前次观测变化</p><strong>{fmt(result.change)}</strong><small>前次 {result.previous?.observation_date||'无'}</small></article><article><p>历史分位 / %</p><strong>{fmt(result.historical_percentile)}</strong><small>此前最多252个有效观测；实际 {result.history_n} 个，非季节调整</small></article><article><p>观测距截止日 / 日历日</p><strong>{fmt(result.age_days)}</strong><small>发布日历未核验，不自动判定过期</small></article></div>
 <Chart rows={result.points} x="observation_date" lines={[[ 'raw_value',item.name+' / '+item.unit ]]}/>
 <h3>历年季节性 / {item.unit}</h3><label className="check"><input type="checkbox" checked={years} onChange={e=>Y(e.target.checked)}/>显示全部年份（默认最近5年）</label><HistoryChart result={{...result.seasonal,unit:item.unit,accessible_label:'基本面历年季节性对比图'}} showAllYears={years}/>
 <p className="sub">季节性按原观测月日对齐；周频不强配同周，月频不改成日频。连线仅辅助观察，不补零、不前填、不插值。分位反映原始水平，不是买卖信号。</p></>}
 <details><summary>逐点核验与版本</summary><p>版本 {result.snapshot_sha256}</p><div className="table-wrap"><table><thead><tr><th>观测期</th><th className="num">原始值 / {item.unit}</th></tr></thead><tbody>{result.points.map(p=><tr key={p.observation_date}><td>{p.observation_date}</td><td className="num">{fmt(p.raw_value)}</td></tr>)}</tbody></table></div></details></>}
 </>}</>}
 </section>;
}

function FundamentalReview({revision}){
 const [data,setData]=useState(null),[error,setError]=useState(''),[code,setCode]=useState(''),[series,setSeries]=useState(null),[seriesError,setSeriesError]=useState(''),[range,setRange]=useState('all');
 const [update,setUpdate]=useState(null),[updateError,setUpdateError]=useState('');
 useEffect(()=>{const c=new AbortController();setUpdate(null);setUpdateError('');api('/api/fundamentals/status',{signal:c.signal}).then(setUpdate).catch(e=>{if(e.name!=='AbortError')setUpdateError(e.message);});return()=>c.abort();},[revision]);
 useEffect(()=>{const c=new AbortController();setData(null);setError('');api('/api/fundamentals/review',{signal:c.signal}).then(d=>{setData(d);setCode(v=>d.indicators.some(i=>i.code===v)?v:d.indicators[0]?.code||'');}).catch(e=>{if(e.name!=='AbortError')setError(e.message);});return()=>c.abort();},[revision]);
 useEffect(()=>{
  const c=new AbortController();setSeries(null);setSeriesError('');
  if(!data||!code)return()=>c.abort();
  (async()=>{let offset=0,points=[];
   do{
    const page=await api('/api/fundamentals/review?'+new URLSearchParams({code,limit:5000,offset}),{signal:c.signal});
    if(page.snapshot_sha256!==data.snapshot_sha256)throw Error('读取期间快照已更新，请点击页面“刷新数据”重新加载。');
    points.push(...page.points);
    if(page.next_offset!==null&&page.next_offset<=offset)throw Error('分页未前进，已停止读取。');
    offset=page.next_offset;
   }while(offset!==null);
   if(!c.signal.aborted)setSeries({code,points});
  })().catch(e=>{if(e.name!=='AbortError')setSeriesError(e.message);});
  return()=>c.abort();
 },[data,code]);
 const item=data?.indicators.find(i=>i.code===code),points=series?.code===code?series.points:[],last=points.at(-1),previous=points.at(-2);
 const cutoff=last&&range!=='all'?new Date(Date.parse(last.observation_date+'T00:00:00Z')-Number(range)*86400000).toISOString().slice(0,10):'';
 const shown=points.filter(p=>!cutoff||p.observation_date>=cutoff);
 return <section className="panel"><h2>基本面 · 隔离审阅</h2><p className="sub">独立于顶部量价筛选 · 不参与交割基差或机会排名；CU另有周频参考价差 · 不触发取数</p>
 <FundamentalUpdateStatus record={update} error={updateError}/>
 {error?<p role="alert">{error}</p>:!data?<p role="status">读取隔离快照…</p>:<>
 <div className="filters"><label>指标<select value={code} onChange={e=>setCode(e.target.value)}>{data.indicators.map(i=><option value={i.code} key={i.code}>{i.name} · {i.code}</option>)}</select></label>
 <label>走势范围<select value={range} onChange={e=>setRange(e.target.value)}><option value="all">全部已验收历史</option><option value="365">最新观测往前一年</option><option value="90">最新观测往前90天</option></select></label></div>
 {item&&<><h3>{item.name}</h3><p className="sub">{item.code} · {item.source} · {{D:'日',W:'周',M:'月',Q:'季',Y:'年'}[item.frequency]} / {item.unit}</p>
 {seriesError?<p role="alert">{seriesError}</p>:!series||series.code!==code?<p role="status">读取指标历史…</p>:!points.length?<p>该指标暂无已验收观测。</p>:<>
 <div className="metrics"><article><p>最新已保存值 / {item.unit}</p><strong>{fmt(last.raw_value)}</strong><small>观测日期 {last.observation_date}，不代表当前实时值</small></article><article><p>较前次观测变化 / {item.unit}</p><strong>{previous?fmt(last.raw_value-previous.raw_value):'—'}</strong><small>{previous?'前次观测 '+previous.observation_date:'仅一个观测，不能计算变化'}</small></article><article><p>当前图中观测数</p><strong>{fmt(shown.length)}</strong><small>{shown[0]?.observation_date} — {shown.at(-1)?.observation_date}</small></article></div>
 <Chart rows={shown} x="observation_date" lines={[[ 'raw_value',item.name+' / '+item.unit ]]}/>
 <p className="sub">按实际观测顺序等距展示；连线仅辅助读图，不代表期间每天有值。不补零、不前填、不插值。最新观测距今 {Math.max(0,Math.floor((Date.now()-Date.parse(last.observation_date+'T00:00:00Z'))/86400000))} 个日历日；低频发布规律未完整核验，不据此自动判为过期。</p>
 </>}<p className="sub">用途限制：{item.limits.join('；')}</p></>}
 <details><summary>全部指标覆盖、证据及原始值</summary><div className="table-wrap"><table><thead><tr><th>指标 / 编码</th><th>频率 / 原单位</th><th>观测覆盖 / 条数</th><th>用途限制</th><th>原始值</th></tr></thead><tbody>{data.indicators.map(i=><tr key={i.code}><td><button onClick={()=>setCode(i.code)}>{i.name}</button><div className="sub">{i.code} · {i.source}</div></td><td>{{D:'日',W:'周',M:'月',Q:'季',Y:'年'}[i.frequency]} / {i.unit}</td><td>{i.first} — {i.last}<div className="sub">{fmt(i.observations)} 条</div></td><td>{i.limits.join('；')}</td><td><a href={'/api/fundamentals/review?code='+i.code+'&limit=5000'} target="_blank" rel="noreferrer">查看 JSON ↗</a></td></tr>)}</tbody></table></div></details>
 <p>继续隔离：{data.excluded.map(i=>i.code+' '+i.name).join('；')}</p><ul>{data.warnings.map(w=><li key={w}>{w}</li>)}</ul></>}
 </section>;
}
function SinglePosition({product,symbol,start,end,revision}){
 const [selected,setSelected]=useState(symbol),[data,setData]=useState(null),[error,setError]=useState('');
 useEffect(()=>{const c=new AbortController();setData(null);setError('');if(!selected)return()=>c.abort();const q=new URLSearchParams({product,symbol:selected});if(start)q.set('start',start);if(end)q.set('end',end);api('/api/contract-position?'+q,{signal:c.signal}).then(d=>{if(!c.signal.aborted)setData(d);}).catch(e=>{if(e.name!=='AbortError')setError(e.message);});return()=>c.abort();},[product,selected,start,end,revision]);
 return <section className="panel"><h2>单合约持仓 · {selected||'未选择'}</h2><p className="sub">从异常证据定位到真实合约。此选择独立于下方两腿换月组合。</p>{error?<p role="alert">{error}</p>:!data?<p role="status">读取合约持仓…</p>:<><label>持仓合约<select value={selected} onChange={e=>setSelected(e.target.value)}>{data.contracts.map(s=><option key={s}>{s}</option>)}</select></label><p>最新有效持仓 {fmt(data.latest?.position)} 手 · 相邻观测日增减 {fmt(data.latest?.change)} 手 · 观测日 {data.latest?.trade_date||'无'}{data.meta.stale?' · 缓存降级':''}</p><Chart rows={data.series} lines={[["position",selected+' 持仓 / 手']]} integer/><Chart rows={data.series} lines={[["change",selected+' 持仓增减 / 手']]} integer bar/><p className="sub">{data.warnings.join('；')}</p></>}</section>;
}
function SeasonalPosition({product,end,revision}){
 const [result,setResult]=useState(null),[error,setError]=useState(''),[showAll,setShowAll]=useState(false);
 useEffect(()=>{const c=new AbortController();setResult(null);setError('');setShowAll(false);api('/api/seasonal-position?'+new URLSearchParams({product,end,all_years:true}),{signal:c.signal}).then(setResult).catch(e=>{if(e.name!=='AbortError')setError(e.message);});return()=>c.abort();},[product,end,revision]);
 return <section className="panel"><h2>季节性持仓 · 品种总持仓</h2><p className="sub">所有已观测真实月合约持仓合计 / 手；按自然月日叠加各年，非主力合约持仓。读取本地截至所选日期的全部历史，不受顶部开始日期限制。</p><label>显示范围<select value={showAll?'all':'recent'} onChange={e=>setShowAll(e.target.value==='all')}><option value="recent">最近5个有数据年份（不足5年全部显示）</option><option value="all">全部已有年份</option></select></label>{error?<p role="alert">{error}</p>:result?<><p className="sub">截止 {result.meta.as_of} {result.meta.stale?' · 缓存降级':''} · 已加载 {result.years.length} 年：{result.years.join('、')||'无'}。默认最近 {Math.min(5,result.years.length)} 年；点击图例可单独显示或隐藏年份，切换显示不重新取数。</p><HistoryChart result={{...result, axis:result.series.map(r=>r.month_day), mode:"seasonal", metric:"position"}} showAllYears={showAll}/><p className="sub">{result.warnings.join('；')}</p></>:<p role="status">正在读取全部历史持仓…</p>}</section>;
}
function RiskPanel({risk}) {
 if (!risk) return null;
 return <section className="panel"><h2>价差变化风险 · 日终历史</h2>
  <p className="sub">{risk.start || '无'} 至 {risk.as_of || '无'} · 限定在所读窗口内的合约对共同历史 · 原始报价单位，非账户盈亏</p>
  <div className="metrics">{risk.volatility.map(v=><article key={v.window}><p>{v.window}期变化波动率</p><strong>{fmt(v.value)}</strong><small>有效变化 {v.n}/{v.window} · 样本标准差{v.value==null?' · 窗口不足或有缺口':''}</small></article>)}</div>
  <h3>固定持有期最大不利变动</h3><div className="table-wrap"><table><thead><tr><th>持有期</th><th className="num">完整窗口 / 历史内缺口排除</th><th className="num">买近卖远</th><th className="num">卖近买远</th></tr></thead><tbody>{risk.horizons.map(h=><tr key={h.horizon}><td>{h.horizon}个观测日</td><td className="num">{h.n} / {h.skipped}{h.n<20?' · 样本少':''}</td>{['buy','sell'].map(direction=><td className="num" key={direction}>{fmt(h[direction]?.value)}{h[direction]&&<div className="sub">起点 {h[direction].entry_date}<br/>最差日 {h[direction].adverse_date}<br/>窗口结束 {h[direction].end_date}</div>}</td>)}</tr>)}</tbody></table></div>
  <p className="sub">数值以非负幅度表示；0表示完整窗口中该方向没有不利变动，—表示无可用结果。重叠窗口不是独立交易，历史最差不是未来损失上限。</p>
  <details><summary>统计口径与缺口</summary><ul>{risk.warnings.map(w=><li key={w}>{w}</li>)}</ul><p>共同历史之前未纳入：{risk.excluded_pre_history??'—'}个品种观测日；不计为历史内缺数。</p><p>共同历史内两腿未配对日期：{risk.missing_dates.length}个{risk.missing_dates.length ? ' · '+risk.missing_dates.join('、') : ''}</p></details>
 </section>;
}
const tabs=['全市场雷达','机会扫描','跨期分析','期限结构','持仓换月','历史合约','自选监测','数据状态','基差分析','基本面分析'];
export function App(){
 const [tab,T]=useState(tabs[0]),[q,Q]=useState({product:'IM',price_type:'close'}),[products,P]=useState([]),[d,D]=useState(null),[err,E]=useState(''),[busy,B]=useState(false),[rev,R]=useState(0),[w,W]=useState([]),[note,N]=useState(''),[candidate,Candidate]=useState(null);
 const qs=new URLSearchParams(Object.entries(q).filter(([k,v])=>v&&k!=='focus_symbol'&&(tab!=='期限结构'||k!=='start')&&(!['机会扫描','期限结构'].includes(tab)||!['near','far'].includes(k)))).toString();
 useEffect(()=>{api('/api/products').then(x=>P(x.products)).catch(e=>E(e.message));api('/api/watchlists').then(x=>W(x.items)).catch(e=>E(e.message));},[]);
 useEffect(()=>{if(['全市场雷达','基差分析','基本面分析'].includes(tab)){B(false);D(null);E('');return;}const c=new AbortController();B(true);E('');D(null);api('/api/dashboard?'+qs,{signal:c.signal}).then(D).catch(e=>{if(e.name!=='AbortError')E(e.message);}).finally(()=>{if(!c.signal.aborted)B(false);});return()=>c.abort();},[qs,rev,tab]);
 const pairPage=!['全市场雷达','基差分析','基本面分析','机会扫描','期限结构'].includes(tab);
 const latest=d?.spread.summary.latest||{},panel=(title,sub,child)=><section className="panel"><h2>{title}</h2><p className="sub">{sub}</p>{child}</section>;
 function pair(near,far){Q({...q,near,far});T('跨期分析');}
 async function save(){try{W((await api('/api/watchlists',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({product:d.product,near:d.near,far:d.far,price_type:d.price_type})})).items);N('已保存本地自选');}catch(e){N(e.message);}}
 return <div className="shell"><aside><div className="brand">∿ CURVE<small>期货套利工作台</small></div><p className="caption">WORKSPACE / 研究与监测</p><nav>{tabs.map((t,i)=><button key={t} className={tab===t?'active':''} onClick={()=>T(t)}><span>{String(i+1).padStart(2,'0')}</span>{t}</button>)}</nav><div className="aside-foot">● LOCAL / READ ONLY<p>生产数据只读<br/>不启动上游更新任务</p></div></aside><main><header><div><div className="eyebrow">FUTURES / RELATIVE VALUE</div><h1>{tab}</h1><p>{({'全市场雷达':'一次查看全品种量价异常，按证据下钻验证。','机会扫描':'筛选值得进一步检查的组合，而不是直接生成交易信号。','跨期分析':'聚焦一个组合：价格驱动、历史风险与成本情景。','期限结构':'观察整条曲线：跨日期变化、相邻价差与量仓分布。','基本面分析':'按品种查看库存、供需及参考价格，保留数据来源与用途限制。'})[tab]||'从期限价差到持仓迁移，保留每一个数据口径。'}</p></div><div><span className="badge">日终研究模式</span>{tab!=='全市场雷达'&&<button disabled={busy} onClick={()=>R(rev+1)}>刷新数据</button>}</div></header>
 {tab!=='全市场雷达'&&<section className="filters"><label>品种<select value={q.product} onChange={e=>Q({product:e.target.value,price_type:q.price_type,...(tab==='基本面分析'?{end:q.end}:{})})}>{!products.length&&<option>IM</option>}{products.map(p=><option key={p.product}>{p.product}</option>)}</select></label>{pairPage&&['near','far'].map((k,i)=><label key={k}>{i?'远月':'近月'}<select value={q[k]||d?.[k]||''} onChange={e=>Q({...q,[k]:e.target.value})}><option value="">自动选取</option>{d?.contracts.map(c=><option key={c.symbol}>{c.symbol}</option>)}</select></label>)}{tab!=='基本面分析'&&<label>价格口径<select value={q.price_type} onChange={e=>Q({...q,price_type:e.target.value})}><option value="close">收盘价</option><option value="settle">结算价</option></select></label>}{(['期限结构','基本面分析'].includes(tab)?['end']:['start','end']).map((k,i)=><label key={k}>{k==='end'?'截止日期':'开始日期'}<input type="date" value={q[k]||''} onChange={e=>Q({...q,[k]:e.target.value})}/></label>)}</section>}
 <div hidden={tab!=='全市场雷达'}><RadarPanel onSelect={(product,event,result,target)=>{Candidate(event?.far?{...event,product,price_type:result.price_type,as_of:result.as_of}:null);Q({product,price_type:result.price_type,start:result.start,end:result.as_of,...(event?.near&&!event.far?{focus_symbol:event.near}:{}),...(event?.far?{near:event.near,far:event.far}:{})});T(target);}}/></div>
 {tab==='基本面分析'&&<FundamentalAnalysis key={q.product} product={q.product} end={q.end} revision={rev}/>}
 {tab==='基差分析'&&<BasisPanel key={q.product} product={q.product} start={q.start} end={q.end} priceType={q.price_type} revision={rev} Chart={Chart} onProduct={product=>Q({product,price_type:q.price_type})}/>}
 {err&&<div className="error" role="alert">读取失败：{err} <button onClick={()=>Q({product:'IM',price_type:'close'})}>重置筛选</button></div>}{busy&&<div className="empty" role="status">正在读取并校验日线快照…</div>}
{d&&<><div className="status"><span>● 源数据 {d.meta.as_of}{pairPage&&<> · 配对日期 {latest.trade_date||'无'} · {d.near} − {d.far}</>}</span><span>{d.meta.stale?'⚠ 缓存降级':'本地 Parquet'}　{pairPage&&<a href={'/api/export?'+qs}>当前组合 CSV ↗</a>}</span></div>{pairPage&&<div className="metrics">{[['跨期价差',fmt(latest.spread),'近月 − 远月 / 原始报价单位'],['较前次配对变动',fmt(latest.change),'不是可成交损益'],['历史分位',latest.historical_percentile==null?'—':fmt(latest.historical_percentile)+'%',`此前${fmt(latest.history_n)}个观测，不含当日${latest.history_n<60?' · 短样本，勿单独决策':''}`],['有效配对样本',fmt(d.spread.summary.sample_count),'两腿同日有效价格']].map(([a,b,c])=><article key={a}><p>{a}</p><strong>{b}</strong><small>{c}</small></article>)}</div>}
 {tab==='机会扫描'&&<ScanPanel data={d} onPair={pair}/>}
 {tab==='跨期分析'&&d.near&&d.far&&<SpreadPanel key={`${d.product}-${d.near}-${d.far}-${q.start||''}`} data={d} start={q.start} revision={rev}/>}
 {tab==='期限结构'&&<CurvePanel key={d.product+d.meta.as_of} Chart={Chart} data={d} onPair={pair}/>}
 {tab==='持仓换月'&&(q.focus_symbol||d.near)&&<SinglePosition key={d.product+(q.focus_symbol||d.near)} product={d.product} symbol={q.focus_symbol||d.near} start={q.start} end={q.end} revision={rev}/>}
 {tab==='持仓换月'&&<details className="panel" key={`${d.product}-${d.near}-${d.far}`}><summary>展开两腿价格 · {d.near} / {d.far}</summary><p className="sub">两腿共用价格轴，与价差图分开；同一价格口径和日期窗口，可识别价差变化来自哪一腿。</p><Chart rows={d.spread.series} lines={[["near_price",d.near],["far_price",d.far]]}/></details>}
 {tab==='持仓换月'&&<>{panel('持仓与成交迁移','远月占比 = 远月 / 所选两腿合计，不等同于投资者实际移仓',<Chart rows={d.rollover.series} lines={[["new_oi_share","远月持仓占比"],["new_volume_share","远月成交占比"]]} percent/>)}{panel('换月价格估计','等手数日线价格比较，未扣除费用和双腿冲击',<Chart rows={d.rollover.series} lines={[["long_roll_estimate","多头换月成本"],["short_roll_estimate","空头换月成本"]]}/>)}</>}
 {tab==='跨期分析'&&<RiskPanel risk={d.risk}/>}
 {tab==='持仓换月'&&d.meta.as_of&&<SeasonalPosition product={d.product} end={d.meta.as_of} revision={rev}/>}
 {tab==='历史合约'&&d.meta.as_of&&<HistoryPanel key={d.product} product={d.product} end={d.meta.as_of} near={d.near} far={d.far} priceType={d.price_type} revision={rev}/>}
 {tab==='跨期分析'&&d.near&&d.far&&<ResearchCard key={[d.product,d.near,d.far,d.price_type].join(':')} data={d} saved={w.find(a=>a.product===d.product&&a.near===d.near&&a.far===d.far&&a.price_type===d.price_type)} candidate={candidate?.near===d.near&&candidate?.far===d.far&&candidate?.price_type===d.price_type&&candidate?.as_of===d.meta.as_of?candidate:null} onSaved={W}/>}
 {tab==='自选监测'&&panel('本地自选组合','点击组合继续核查与成本测算；SQLite 持久化，不自动通知或下单',<>
 <button className="primary" disabled={!d.near||!d.far} onClick={save}>＋ 收藏当前组合</button>
 <div className="table-wrap"><table><thead><tr><th>品种 / 组合</th><th>研究方向与原因</th><th>目标 / 失效条件</th><th>复核日期</th><th>操作</th></tr></thead><tbody>{w.map(a=><tr key={a.id}>
 <td><button className="link" onClick={()=>{Candidate(null);Q({product:a.product,near:a.near,far:a.far,price_type:a.price_type});T('跨期分析');}}>{a.near} / {a.far}</button><small className="block-note">{a.price_type==='close'?'收盘':'结算'} · 参考 {a.plan?.reference_date||'尚未记录'}</small></td>
 <td>{a.plan?.direction==='buy'?'买近卖远':a.plan?.direction==='sell'?'卖近买远':'待核查'}<small className="block-note">{a.plan?.rationale||'未填写原因'}</small></td>
 <td>{fmt(a.plan?.exit_spread)}<small className="block-note">{a.plan?.invalidation||'未填写失效条件'}</small></td><td>{a.plan?.review_date||'未设置'}</td>
 <td><button onClick={async()=>{try{W((await api('/api/watchlists/'+a.id,{method:'DELETE'})).items);}catch(e){N(e.message);}}}>移除</button></td></tr>)}</tbody></table></div>{!w.length&&<div className="empty">尚无自选组合</div>}</>)}
 {tab==='数据状态'&&panel('数据血缘与运行边界','读取本地量价；不触发生产更新，不连接 RDS 写入',<><dl>{Object.entries(d.meta).map(([k,v])=><div key={k}><dt>{k}</dt><dd>{typeof v==='object'?JSON.stringify(v):String(v)}</dd></div>)}</dl><ul>{d.warnings.map(a=><li key={a}>{a}</li>)}</ul><p>基本面身份待核验；Wind 实时订阅、自动告警、交易下单均未启用。</p></>)}<div className="notice">日线不是实时可成交报价。费用、保证金、交割资格及交易时段须独立核验。缺失数据不填零。</div></>}
 {tab==='数据状态'&&<><FundamentalReview revision={rev}/><FundamentalCandidates revision={rev} Chart={Chart}/></>}
 {note&&<div className="toast" role="status">{note}<button onClick={()=>N('')}>关闭</button></div>}<footer>CURVE / 本地期货研究工作台<span>真实月合约 · 缺失不补值 · 口径可追溯</span></footer></main></div>;
}
