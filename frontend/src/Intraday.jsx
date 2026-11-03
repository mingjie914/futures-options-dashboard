import {memo,useEffect,useRef,useState} from 'react';
import {usePreference} from './preferences.js';
import {liveOnline,openLive} from './intraday-live.js';

const BASE=import.meta.env.VITE_INTRADAY_BASE?.replace(/\/$/,'');
const VIEWS={basis:'股指基差',options:'期权曲线'};
const number=(value,digits=2)=>Number.isFinite(value)?value.toLocaleString('zh-CN',{maximumFractionDigits:digits}):'—';
const tone=value=>value>0?'var(--positive)':value<0?'var(--negative)':'inherit';
const time=value=>value?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'—';

const IndexBasis=memo(function IndexBasis({code,metadata,quotes}) {
  const meta=metadata[code],spot=quotes.get(code),contracts=Object.entries(metadata).filter(([,m])=>m.kind==='future'&&m.underlying===code);
  return <section><div className="observation-section-heading"><h2>{meta.name||code}</h2><strong>{number(spot?.last)}</strong></div>
    {contracts.length?<table style={{width:'100%',borderCollapse:'collapse',fontSize:14,textAlign:'right'}}><thead><tr>{['合约','最新','基差','年化','剩余天数'].map((label,i)=><th key={label} style={{textAlign:i?'right':'left',padding:'8px 0',fontWeight:500,color:'var(--muted)'}}>{label}</th>)}</tr></thead><tbody>{contracts.map(([contract,m])=>{
      const q=quotes.get(contract),basis=Number.isFinite(spot?.last)&&Number.isFinite(q?.last)?spot.last-q.last:null;
      const sampled=q?.acquired_at?.slice(0,10),days=m.last_trade_date&&sampled?(Date.parse(m.last_trade_date)-Date.parse(sampled))/86400000:null;
      const annual=days>0&&spot?.last>0&&Number.isFinite(basis)?basis/spot.last*365/days*100:null;
      return <tr key={contract}><th style={{textAlign:'left',padding:'10px 0',fontWeight:500}}>{contract.split('.')[0]}</th><td>{number(q?.last)}</td><td style={{color:tone(basis)}}>{number(basis)}</td><td style={{color:tone(annual)}}>{number(annual)}{Number.isFinite(annual)?'%':''}</td><td>{number(days,0)}</td></tr>;
    })}</tbody></table>:<p className="sub">股指期货待接入</p>}
    {spot?.acquired_at&&<p className="sub">采样 {time(spot.acquired_at)}</p>}
  </section>;
});

function LiveStatus({feed}) {
  const [now,setNow]=useState(()=>performance.now());
  useEffect(()=>{if(!BASE)return;const timer=setInterval(()=>setNow(performance.now()),1000);return()=>clearInterval(timer);},[]);
  const online=liveOnline(feed,now),status=!BASE?'等待独立Wind实时订阅接入':['waiting','connecting'].includes(feed.connection)?'正在连接Wind订阅':feed.connection==='unauthorized'?'只读订阅未授权':online?'订阅在线':feed.connection==='connected'?'等待Wind发送端':'连接中断，自动重连';
  return <p className="sub" role="status">{status}{!online&&feed.quotes.size>0?' · 保留最近值':''}</p>;
}

export default function Intraday() {
  const [saved,save]=usePreference('intraday.view','basis'),[view,setView]=useState(()=>{const value=new URLSearchParams(location.search).get('view')||saved;return value in VIEWS?value:'basis';});
  const [feed,setFeed]=useState({quotes:new Map(),connection:'waiting'});
  const live=useRef();
  useEffect(()=>{if(BASE){live.current=openLive(BASE,view,setFeed);return()=>live.current.close();}},[]);
  useEffect(()=>{live.current?.select(view);},[view]);
  useEffect(()=>{save(view);history.replaceState({},'',location.pathname+'?'+new URLSearchParams({view}));},[view]);
  const metadata=feed.catalog?.metadata||{},indices=(feed.catalog?.codes||[]).filter(code=>metadata[code]?.kind==='index');
  return <><div className="board-content" style={{paddingBottom:0}}><nav className="board-tabs" aria-label="日内页签">{Object.entries(VIEWS).map(([key,label])=><button key={key} aria-pressed={view===key} onClick={()=>setView(key)}>{label}</button>)}</nav></div><main className="board-content observation-page">
    <div className="observation-heading"><h1>{VIEWS[view]}</h1><span>开发预览</span></div>
    <LiveStatus feed={feed}/>
    {view==='basis'?<div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(min(100%,420px),1fr))',gap:'20px 48px'}}>{feed.health?.source_mode==='wind'&&indices.map(code=><IndexBasis key={code} code={code} metadata={metadata} quotes={feed.quotes}/>)}</div>:<p className="sub">等待期权实时行情与当日分时序列</p>}
  </main></>;
}
