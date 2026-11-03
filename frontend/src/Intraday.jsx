import './intraday.css';
import {lazy,memo,Suspense,useEffect,useMemo,useRef,useState} from 'react';
import {basisRows,formatNumber as number,liveOnline,openLive,optionSeries} from './intraday-live.js';
import {usePreference} from './preferences.js';
import {SpreadMatrix} from './SpreadMatrix.jsx';

const BASE=import.meta.env.VITE_INTRADAY_BASE?.replace(/\/$/,'');
const OptionCurves=lazy(()=>import('./IntradayOptions.jsx'));
const percent=value=>number(value)+(Number.isFinite(value)?'%':'');
const products={IO:'沪深300',HO:'上证50',MO:'中证1000'};

const IndexBasis=memo(function IndexBasis({group:{code,metadata},quotes}) {
  const meta=metadata[code],spot=quotes.get(code),rows=basisRows(code,metadata,quotes),rates=rows.map(r=>r.annual).filter(Number.isFinite),lo=Math.min(0,...rates),hi=Math.max(0,...rates),range=hi-lo||1;
  const asOf=spot?.acquired_at?.slice(0,10),matrix={meta:{as_of:asOf},contracts:rows.map(r=>({symbol:r.contract.split('.')[0],close:quotes.get(r.contract)?.acquired_at?.slice(0,10)===asOf?r.last:null,trade_date:asOf})),contract_terms:{as_of:asOf,contracts:rows.map(r=>({symbol:r.contract.split('.')[0],last_trade_date:metadata[r.contract].last_trade_date,status:'wind_recorded'}))}};
  return <section className="intraday-product"><div className="intraday-product-heading"><h2>{meta.name||code}</h2><strong>{number(spot?.last)}</strong></div><div className="intraday-pair"><SpreadMatrix data={matrix} title={null}/><div>
    {rows.length?<div style={{overflowX:'auto'}}><table className="intraday-basis"><colgroup>{[60,12,12,18,14,15,17,12].map((width,i)=><col key={i} className={i===0?'intraday-contract':undefined} style={{width:i===0?width:width+'%'}}/>)}</colgroup><thead><tr>{['合约','最新价','基差','年化基差','绝对收益','日均','持仓量','到期天数'].map((label,i)=><th key={label} className={i===0?'intraday-contract':undefined}>{label}</th>)}</tr></thead><tbody>{rows.map(r=><tr key={r.contract}><th scope="row" className="intraday-contract">{r.contract.split('.')[0]}</th><td>{number(r.last)}</td><td>{number(r.basis)}</td><td style={{position:'relative'}}>{Number.isFinite(r.annual)&&<span aria-hidden="true" style={{position:'absolute',insetBlock:7,left:(Math.min(0,r.annual)-lo)/range*100+'%',width:Math.abs(r.annual)/range*100+'%',background:'#4e7fb947',borderRadius:2}}/>}<span style={{position:'relative'}}>{percent(r.annual)}</span></td><td>{percent(r.absolute)}</td><td>{number(r.daily)}</td><td>{number(r.oi,0)}</td><td>{number(r.days,0)}</td></tr>)}</tbody></table></div>:<p className="sub">股指期货待接入</p>}
  </div></div></section>;
},(a,b)=>a.group===b.group&&a.group.codes.every(code=>a.quotes.get(code)===b.quotes.get(code)));

function LiveStatus({feed}) {
  const [now,setNow]=useState(()=>performance.now());
  useEffect(()=>{if(!BASE)return;const timer=setInterval(()=>setNow(performance.now()),1000);return()=>clearInterval(timer);},[]);
  const online=liveOnline(feed,now),status=!BASE?'等待实时行情接入':['waiting','connecting'].includes(feed.connection)?'连接中':feed.connection==='unauthorized'?'订阅未授权':online?'订阅在线':feed.connection==='connected'?'等待行情':'连接中断，自动重连';
  return online?null:<p className="sub" role="status">{status}{feed.quotes.size>0?' · 保留最近值':''}</p>;
}

export default function Intraday() {
  const [feed,setFeed]=useState(()=>({quotes:new Map(),connection:'waiting'}));
  const [saved,save]=usePreference('intraday.series','MO2611'),[selection,select]=useState(()=>new URLSearchParams(location.search).get('series')||saved),live=useRef();
  const available=useMemo(()=>optionSeries(feed.catalog),[feed.catalog]),product=products[selection.slice(0,2)]?selection.slice(0,2):'MO',months=available.filter(s=>s.startsWith(product)),series=months.includes(selection)?selection:months[0]||product;
  useEffect(()=>{if(BASE){const client=openLive(BASE,setFeed,selection);live.current=client;return client.close;}},[]);
  useEffect(()=>{if(months.length&&selection!==series)select(series);if(/^(IO|HO|MO)\d{4}$/.test(series))live.current?.select(series);save(selection);const query=new URLSearchParams(location.search);query.delete('view');query.set('series',selection);history.replaceState({},'',location.pathname+'?'+query);},[series,selection]);
  const groups=useMemo(()=>['000016.SH','000300.SH','000905.SH','000852.SH'].filter(code=>feed.catalog?.metadata[code]?.kind==='index').map(code=>{
    const metadata=Object.fromEntries(Object.entries(feed.catalog.metadata).filter(([c,m])=>c===code||m.kind==='future'&&m.underlying===code));
    return {code,metadata,codes:Object.keys(metadata)};
  }),[feed.catalog]);
  return <main className="board-content observation-page" aria-label="日内">
    <LiveStatus feed={feed}/>
    <div className="filters observation-controls"><select aria-label="股指期权" value={product} onChange={e=>select(available.find(s=>s===e.target.value+series.slice(2))||available.find(s=>s.startsWith(e.target.value))||e.target.value)}>{Object.entries(products).map(([p,name])=><option key={p} value={p}>{p}/{name}</option>)}</select><div className="intraday-months" role="group" aria-label="期权月份">{months.length?months.map(s=><button key={s} type="button" aria-pressed={series===s} onClick={()=>select(s)}>{s.slice(2)}</button>):<span className="sub">月份待接入</span>}</div></div>
    <Suspense fallback={<p className="sub" style={{height:376}}>加载中…</p>}><OptionCurves catalog={feed.catalog} quotes={feed.quotes} series={series} intraday={feed.intraday?.series===series?feed.intraday:null}/></Suspense>
    {feed.health?.source_mode==='wind'&&groups.map(group=><IndexBasis key={group.code} group={group} quotes={feed.quotes}/>)}
  </main>;
}
