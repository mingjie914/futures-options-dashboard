import './intraday.css';
import {memo,useEffect,useMemo,useRef,useState} from 'react';
import {basisComparison,formatNumber as number,liveMatrix,liveOnline,openLive,optionSeries} from './intraday-live.js';
import {usePreference} from './preferences.js';
import {formatPrice as price} from './charts.js';
import {SpreadMatrix} from './SpreadMatrix.jsx';

const BASE=import.meta.env.VITE_INTRADAY_BASE?.replace(/\/$/,'');
import OptionCurves from './IntradayOptions.jsx';
const percent=value=>price(value)+(Number.isFinite(value)?'%':'');
const signedPrice=value=>Number.isFinite(value)?(value>0?'+':'')+price(value):'—';
const signedPercent=value=>signedPrice(value)+(Number.isFinite(value)?'%':'');
const bounds=(values,centered=false)=>{const numbers=values.filter(Number.isFinite),low=Math.min(0,...numbers),high=Math.max(0,...numbers),radius=Math.max(-low,high)||1;return centered?[-radius,2*radius]:[low,high-low||1];};
function Rate({value,limits,signed=false}) {
  return <span className={'intraday-rate'+(signed&&value>0?' direction-long':signed&&value<0?' direction-short':'')}>
    {Number.isFinite(value)&&<span className="intraday-rate-bar" aria-hidden="true" style={{left:(Math.min(0,value)-limits[0])/limits[1]*100+'%',width:Math.abs(value)/limits[1]*100+'%',...(signed?{background:value<0?'#216c562d':'#a43d392d'}:{})}}/>}<span>{signed?signedPercent(value):percent(value)}</span>
  </span>;
}
const products={IO:'沪深300',HO:'上证50',MO:'中证1000'};
const units={'CNY/g':'元/克','CNY/kg':'元/千克','CNY/t':'元/吨','CNY/bbl':'元/桶'};
const sameQuotes=(a,b,codes)=>codes.every(code=>a.get(code)===b.get(code));
const sameGroup=(a,b)=>a.group===b.group&&sameQuotes(a.quotes,b.quotes,a.group.codes);

const CommodityMatrix=memo(function CommodityMatrix({group:{code,metadata,codes},quotes}) {
  const meta=metadata[codes[0]];
  return <section className="intraday-product intraday-commodity"><div className="intraday-product-heading"><h2>{code}/{meta.product_name}</h2><span className="sub">{units[meta.unit]||meta.unit}</span></div><SpreadMatrix data={liveMatrix(metadata,quotes)} title={null}/></section>;
},sameGroup);

const IndexBasis=memo(function IndexBasis({group:{code,metadata},quotes,rows,strength}) {
  const meta=metadata[code],spot=quotes.get(code),limits=bounds(rows.map(r=>r.annual));
  const matrix=liveMatrix(metadata,quotes,code);
  return <section className="intraday-product"><div className="intraday-product-heading"><h2>{meta.name||code}</h2><strong>{price(spot?.last)}</strong>{strength&&<span className="intraday-strength" title="涨跌点数 / 涨跌幅 / 较中证1000（百分点）"><span className={strength.change>0?'direction-long':strength.change<0?'direction-short':undefined}>{signedPrice(Number.isFinite(strength.change)?spot.last-spot.prev_close:null)} / {signedPercent(strength.change)}</span><span className="sub"> / </span><span>{signedPercent(strength.difference)}</span></span>}</div><div className="intraday-pair"><SpreadMatrix data={matrix} title={null}/><div>
    {rows.length?<div className="intraday-basis-wrap"><table className="intraday-basis"><colgroup><col className="intraday-contract"/><col/><col/><col/><col/></colgroup><thead><tr><th className="intraday-contract">合约</th><th>年化基差</th><th title="现货 − 期货">基差</th><th title="绝对收益（万元/手）/ 日均（元/手/日）">绝对 / 日均收益</th><th title="持仓手数 / 剩余自然日">持仓量 / 天数</th></tr></thead><tbody>{rows.map(r=><tr key={r.contract}><th scope="row" className="intraday-contract">{r.contract.split('.')[0]}</th><td><Rate value={r.annual} limits={limits}/></td><td>{price(r.basis)}</td><td>{price(r.absolute,1,1)} / {price(r.daily,1,1)}</td><td>{number(r.oi,0)} / {price(r.days,0)}</td></tr>)}</tbody></table></div>:<p className="sub">股指期货待接入</p>}
  </div></div></section>;
},(a,b)=>sameGroup(a,b)&&a.strength?.change===b.strength?.change&&a.strength?.difference===b.strength?.difference);

const IndexMarket=memo(function IndexMarket({groups,quotes}) {
  const data=basisComparison(groups,quotes),difference=bounds(data.months.flatMap(m=>m.rows.map(r=>r.difference)),true);
  return <div className="intraday-indexes"><div>{groups.map((group,i)=><IndexBasis key={group.code} group={group} quotes={quotes} rows={data.books[i].rows} strength={data.strength[i]}/>)}</div><section className="intraday-compare" aria-label="同月基差对照">
    {data.months.map(({month,rows})=><section key={month} className="intraday-comparison-month" aria-label={month+'同月基差'}><div className="intraday-product-heading"><h2>{month}</h2></div>
      <table className="intraday-basis"><colgroup><col/><col/></colgroup><thead><tr><th>品种</th><th title="年化基差 − IM年化基差（百分点）">较IM</th></tr></thead><tbody>{rows.map(r=><tr key={r.product}><th scope="row" title={r.name+' · '+r.contract.split('.')[0]+(Number.isFinite(r.nominal)?' · 每手'+price(r.nominal/10000)+'万元':'')}>{r.product}</th><td><Rate value={r.difference} limits={difference} signed/></td></tr>)}</tbody></table>
    </section>)}
  </section></div>;
},(a,b)=>a.groups===b.groups&&a.groups.every(group=>sameQuotes(a.quotes,b.quotes,group.codes)));

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
  useEffect(()=>{if(BASE){const client=openLive(BASE,setFeed,selection,import.meta.env.VITE_INTRADAY_BOOTSTRAP_BASE,import.meta.env.VITE_INTRADAY_PRIMARY_BASE);live.current=client;return client.close;}},[]);
  useEffect(()=>{if(months.length&&selection!==series)select(series);if(/^(IO|HO|MO)\d{4}$/.test(series))live.current?.select(series);save(selection);const query=new URLSearchParams(location.search);query.delete('view');query.set('series',selection);history.replaceState({},'',location.pathname+'?'+query);},[series,selection]);
  const groups=useMemo(()=>{
    const entries=(feed.catalog?.codes||[]).map(code=>[code,feed.catalog.metadata[code]]);
    return ['000016.SH','000300.SH','000905.SH','000852.SH','AU','AG','CU','SC','JM','RB','M'].map(code=>{
      const index=feed.catalog?.metadata[code]?.kind==='index',selected=feed.catalog?.commodity_selection?.[code],metadata=Object.fromEntries(entries.filter(([c,m])=>index?c===code||m.kind==='future'&&m.underlying===code:m.kind==='future'&&m.product===code&&(!selected||selected.includes(c))));
      return {code,index,metadata,codes:Object.keys(metadata)};
    }).filter(group=>group.codes.length);
  },[feed.catalog]);
  const indexGroups=useMemo(()=>groups.filter(group=>group.index),[groups]);
  return <main className="board-content observation-page intraday-page" aria-label="日内">
    <div className="filters observation-controls"><select aria-label="股指期权" value={product} onChange={e=>select(available.find(s=>s===e.target.value+series.slice(2))||available.find(s=>s.startsWith(e.target.value))||e.target.value)}>{Object.entries(products).map(([p,name])=><option key={p} value={p}>{p}/{name}</option>)}</select><div className="intraday-months" role="group" aria-label="期权月份">{months.length?months.map(s=><button key={s} type="button" aria-pressed={series===s} onClick={()=>select(s)}>{s.slice(2)}</button>):<span className="sub">月份待接入</span>}</div></div>
    <OptionCurves catalog={feed.catalog} quotes={feed.quotes} series={series} intraday={feed.intraday?.series===series?feed.intraday:null}/>
    {feed.health?.source_mode==='wind'&&<>
      <IndexMarket groups={indexGroups} quotes={feed.quotes}/>
      <div className="intraday-options intraday-commodities">{groups.filter(group=>!group.index).map(group=><CommodityMatrix key={group.code} group={group} quotes={feed.quotes}/>)}</div>
    </>}
    <LiveStatus feed={feed}/>
  </main>;
}
