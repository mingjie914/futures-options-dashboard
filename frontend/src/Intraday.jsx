import {useEffect,useRef,useState} from 'react';
import {ChartFrame,RefreshButton,useChart,usePreference,useResource} from './charts.js';

const names={IH:'上证50',IF:'沪深300',IC:'中证500',IM:'中证1000'};
const number=v=>Number.isFinite(v)?v.toLocaleString('zh-CN',{maximumFractionDigits:2}):'—';
export function basisMetrics(spot,price,expiry,asof) {
  const days=(Date.parse(expiry)-Date.parse(asof))/86400000,basis=Number.isFinite(spot)&&spot>0&&Number.isFinite(price)&&price>0?spot-price:null;
  return {basis,days:Number.isFinite(days)?days:null,annual:basis!=null&&days>0?basis/spot*365/days*100:null,daily:basis!=null&&days>0?basis/days:null};
}

function IntradayChart({month,smile=false}) {
  const ref=useRef(),rows=smile?month.curve:month.history;
  useChart(ref,chart=>{
    const lines=smile?[['iv','当前IV','#4e7fb9'],['previous_iv','前日收盘IV','#a48b59'],['iv_bid','买价IV','#2b8167'],['iv_ask','卖价IV','#b64d49']]:[['price',month.symbol,'#4e7fb9'],['iv','平值IV','#a48b59']];
    chart.setOption({animation:false,legend:{type:'scroll',top:0,left:'center',width:'85%'},tooltip:{trigger:'axis',confine:true,renderMode:'richText',formatter:params=>[params[0]?.axisValue,...params.filter(p=>Number.isFinite(p.value)).map(p=>`${p.seriesName}：${number(p.value*(p.seriesName.includes('IV')?100:1))}${p.seriesName.includes('IV')?'%':''}`)].join('\n')},
      grid:{left:60,right:60,top:40,bottom:56},xAxis:{type:'category',boundaryGap:smile,data:rows.map(p=>smile?p.strike:p.date),axisLabel:{hideOverlap:true}},
      yAxis:[{type:'value',scale:true,axisLabel:{formatter:v=>smile?number(v*100)+'%':number(v)},splitLine:{lineStyle:{color:'#eef0f3'}}},{type:'value',scale:!smile,min:smile?0:undefined,axisLabel:{formatter:v=>smile?number(v):number(v*100)+'%'},splitLine:{show:false}}],
      dataZoom:[{type:'inside'},{type:'slider',bottom:8,height:18}],series:[...lines.map(([key,name,color],i)=>({name,type:'line',yAxisIndex:smile?0:i,showSymbol:smile,symbol:i===2?'triangle':i===3?'triangle':'circle',symbolRotate:i===3?180:0,symbolSize:5,connectNulls:false,itemStyle:{color},lineStyle:{width:i>1?0:2,type:i===1&&smile?'dashed':'solid'},z:4,data:rows.map(p=>p[key])})),...(smile?[['call_oi','认购持仓','#b64d49'],['put_oi','认沽持仓','#2b8167']].map(([key,name,color])=>({name,type:'bar',yAxisIndex:1,barMaxWidth:14,itemStyle:{color,opacity:.2},data:rows.map(p=>p[key])})):[])]},true);
  },[month,smile]);
  return <ChartFrame><div ref={ref} className="observation-chart" role="img" aria-label={smile?'行权价IV与持仓':'价格与平值IV日线样本'}/></ChartFrame>;
}

export default function Intraday() {
  const query=new URLSearchParams(location.search),[savedView,saveView]=usePreference('intraday.view','basis'),[savedSymbol,saveSymbol]=usePreference('intraday.contract','IM2612');
  const [view,setView]=useState(()=>['basis','options'].includes(query.get('view')||savedView)?query.get('view')||savedView:'basis'),[symbol,setSymbol]=useState(query.get('contract')||savedSymbol),[revision,refresh]=useState(0);
  const {data,error,busy}=useResource('/api/futures/v1/intraday-preview?view='+view,revision,view),month=data?.months?.find(m=>m.symbol===symbol)||data?.months?.[0];
  useEffect(()=>{saveView(view);saveSymbol(symbol);history.replaceState({},'',location.pathname+'?'+new URLSearchParams({view,...(view==='options'?{contract:symbol}:{})}));},[view,symbol]);
  return <><style>{`.intraday-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px 48px}.intraday-table{width:100%;border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums}.intraday-table :is(th,td){padding:7px 2px;text-align:right;white-space:nowrap}.intraday-table th{color:var(--muted);font-weight:450}.intraday-table :is(th,td):first-child{text-align:left}.intraday-table td:first-child{font-weight:550}@media(max-width:800px){.intraday-grid{grid-template-columns:1fr}}`}</style><div className="board-content" style={{paddingBottom:0}}><nav className="board-tabs" aria-label="日内页签">{Object.entries({basis:'股指基差',options:'期权曲线'}).map(([key,label])=><button key={key} aria-pressed={view===key} onClick={()=>setView(key)}>{label}</button>)}</nav></div><main className="board-content observation-page" aria-busy={busy}>
    <div className="observation-heading"><h1>{view==='basis'?'股指基差':'期权曲线'}</h1><span>开发预览 · 行情尚未连接</span></div>
    <div className="filters observation-controls">{month&&<select aria-label="合约" value={month.symbol} onChange={e=>setSymbol(e.target.value)}>{data.months.map(m=><option key={m.symbol}>{m.symbol}</option>)}</select>}<span className="observation-event-count">{data?.as_of}{data?.as_of&&(view==='basis'?' · 收盘快照':' · 保存样本')}</span><RefreshButton busy={busy} onClick={()=>refresh(revision+1)}/></div>
    {error?<p role="alert">{error}</p>:!data?<p role="status">读取预览快照…</p>:data.missing?<p>{data.missing}</p>:view==='basis'?<div className="intraday-grid">{data.products.map(p=><section key={p.product}><div className="observation-section-heading"><h2>{p.product}/{names[p.product]}</h2><span className="observation-event-count">现货 {number(p.spot)}</span></div><table className="intraday-table"><thead><tr>{['合约','价格','基差','年化%','日均','剩余天'].map(label=><th key={label}>{label}</th>)}</tr></thead><tbody>{p.contracts.map(c=>{const m=basisMetrics(p.spot,c.price,c.last_trade_date,data.as_of);return <tr key={c.symbol}><td>{c.symbol}</td><td>{number(c.price)}</td><td>{number(m.basis)}</td><td>{number(m.annual)}</td><td>{number(m.daily)}</td><td title={c.last_trade_date||undefined}>{number(m.days)}</td></tr>;})}</tbody></table></section>)}</div>:month?<><section className="observation-price"><div className="observation-section-heading"><h2>价格与隐波</h2><span className="observation-event-count">日线 · 平值IV</span></div><IntradayChart month={month}/></section><section className="observation-price"><div className="observation-section-heading"><h2>行权价与隐波</h2><span className="observation-event-count">隐含远期 {number(month.forward)}</span></div><IntradayChart month={month} smile/></section><p className="sub">已有MO测试样本：2个月份、各5档；分时待接入。</p></>:<p>暂无期权曲线样本</p>}
    {view==='basis'&&<p className="sub">基差 = 现货 − 期货；年化按剩余自然日、365天计算，未扣分红。</p>}
  </main></>;
}
