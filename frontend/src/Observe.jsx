import {useEffect,useMemo,useRef,useState} from 'react';
import {ChartFrame,Metric,RefreshButton,useChart,usePreference,useResource,formatPrice} from './charts.js';
import {rememberProduct,selectedProduct} from './preferences.js';

const products={IF:'沪深300',IH:'上证50',IC:'中证500',IM:'中证1000'},bonds={TS:'2年国债',TF:'5年国债',T:'10年国债',TL:'30年国债'};
const format=(v,percent=false,useGrouping=!percent)=>v==null?'—':(v*(percent?100:1)).toLocaleString('zh-CN',{maximumFractionDigits:2,useGrouping})+(percent?'%':'');
const indices={'000001.SH':'上证指数','000016.SH':'上证50','399330.SZ':'深证100','000300.SH':'沪深300','000905.SH':'中证500','000852.SH':'中证1000','000985.CSI':'中证全指','399006.SZ':'创业板指','000688.SH':'科创50'};
const flowGroups={individual:'个人为主',institution:'机构为主'};
const valuationLines=[{key:'close',name:'指数点位',color:'#4e7fb9'},{key:'lower',name:'低估参考',color:'#568f84',dashed:true},{key:'deep',name:'深估参考',color:'#b64d49',dashed:true},{key:'upper',name:'高估参考',color:'#a48b59',dashed:true}];
const cycleScale=value=>value>0?Math.log10(1+value):value;
const cycleValue=value=>value>0?10**value-1:value;
const bondExtreme=p=>!Number.isFinite(p.bond_return20)?0:Number.isFinite(p.bond_p90)&&p.bond_return20>p.bond_p90?1:Number.isFinite(p.bond_p10)&&p.bond_return20<p.bond_p10?-1:0;
function ObservationChart({rows,field,label,percent=false,zoomGroup}) {
  const ref=useRef(),bond=field==='bond_return20',stock=field==='stock_close';
  const bands=bond?[['90%分位','bond_p90','#aa7946'],['10%分位','bond_p10','#2b8167']]:[['国债偏强',1,'#aa7946'],['国债偏弱',-1,'#2b8167']];
  useChart(ref,chart=>chart.setOption({animation:false,color:['#4e7fb9'],
    tooltip:{trigger:'axis',confine:true,renderMode:'richText',formatter:params=>{const p=rows[params[0]?.dataIndex];return p?[p.trade_date,...[[stock?p.stock_symbol:label,p[field],percent],...(bond?[['90%分位',p.bond_p90,true],['10%分位',p.bond_p10,true]]:[]),['历史分位',p.bond_percentile==null?null:p.bond_percentile/100,true]].filter(([,v])=>Number.isFinite(v)).map(([name,v,percent])=>name+'：'+(stock&&name===p.stock_symbol?formatPrice(v):format(v,percent))),...(stock&&bondExtreme(p)?['国债偏'+(bondExtreme(p)>0?'强':'弱')]:[])].join('\n'):'';}},
    legend:bond||stock?{top:0,left:'center',data:[label,...bands.map(([name])=>name)]}:undefined,
    grid:{left:62,right:20,top:bond||stock?32:12,bottom:56},xAxis:{type:'category',data:rows.map(p=>p.trade_date),boundaryGap:false},
    yAxis:{type:'value',scale:true,axisLabel:{formatter:v=>stock?formatPrice(v):format(v,percent)},splitLine:{lineStyle:{color:'#eef0f3'}}},
    dataZoom:[{type:'inside'},{type:'slider',bottom:8,height:18}],
    series:[{name:label,type:'line',showSymbol:false,connectNulls:false,lineStyle:{width:2},data:rows.map(p=>p[field])},...bands.map(([name,key,color])=>({name,type:'line',showSymbol:stock,showAllSymbol:stock,symbol:'circle',symbolSize:8,connectNulls:false,itemStyle:{color,borderColor:'#fff',borderWidth:1},lineStyle:bond?{width:1.6,type:'dashed'}:{opacity:0},z:stock?4:2,data:rows.map(p=>bond?p[key]:bondExtreme(p)===key?p.stock_close:null)}))]
  },true),[rows,field,label,percent],zoomGroup);
  return <ChartFrame><div ref={ref} className="observation-chart" role="img" aria-label={label}/></ChartFrame>;
}

function StockBondObserve() {
  const query=new URLSearchParams(location.search),preferred=query.get('product')||selectedProduct();
  const [product,setProduct]=useState(products[preferred]?preferred:'IM');
  const [savedBond,saveBond]=usePreference('observe.bond','T');
  const [bond,setBond]=useState(()=>{const value=query.get('bond')??savedBond;return bonds[value]?value:'T';});
  useEffect(()=>saveBond(bond),[bond]);
  const [savedEnd,saveEnd]=usePreference('observe.end','');
  const [end,setEnd]=useState(query.get('end')??savedEnd);
  useEffect(()=>saveEnd(end),[end]);
  const [revision,refresh]=useState(0);
  const {data,error,busy}=useResource('/api/futures/v1/observation?'+new URLSearchParams({product,bond,...(end?{end}:{})}),revision);
  const rows=data?.series||[],latest=rows.at(-1),zoomGroup=useMemo(()=>new Set(),[data]);
  const strong=rows.filter(p=>bondExtreme(p)>0).length,weak=rows.filter(p=>bondExtreme(p)<0).length,correlation=latest?.negative_correlation==null?null:-latest.negative_correlation;
  useEffect(()=>{rememberProduct(product);history.replaceState({},'',location.pathname+'?'+new URLSearchParams({product,bond,...(end?{end}:{})}));},[product,bond,end]);
  return <main className="board-content observation-page" aria-busy={busy}>
    <div className="observation-heading"><h1>国债强弱</h1></div>
    <div className="filters observation-controls">
      <select aria-label="股指期货" value={product} onChange={e=>setProduct(e.target.value)}>{Object.entries(products).map(([p,name])=><option key={p} value={p}>{p}/{name}</option>)}</select>
      <select aria-label="国债期货" value={bond} onChange={e=>setBond(e.target.value)}>{Object.entries(bonds).map(([p,name])=><option key={p} value={p}>{p}/{name}</option>)}</select>
      <input aria-label="观察日" type="date" value={end||data?.as_of||''} onChange={e=>setEnd(e.target.value)}/>
      <RefreshButton busy={busy} onClick={()=>refresh(revision+1)}/>
    </div>
    {error?<p role="alert">{error}</p>:!data?<p role="status">读取股债日线…</p>:!rows.length?<p>{data.missing||'暂无数据'}</p>:<>
      <div className="observation-section-heading">
        <Metric label="国债20日涨幅" value={format(latest?.bond_return20,true)}/>
        <Metric label="历史分位" value={format(latest?.bond_percentile==null?null:latest.bond_percentile/100,true)}/>
        <Metric label="20日相关" value={format(correlation,false,false)}/>
      </div>
      <section className="observation-price"><div className="observation-section-heading"><h2>{data.product} 走势与国债强弱</h2><span className="observation-event-count">偏强{strong}日 · 偏弱{weak}日</span></div><ObservationChart rows={rows} field="stock_close" label="股指主力收盘" zoomGroup={zoomGroup}/></section>
      <section className="observation-price"><div className="observation-section-heading"><h2>{data.bond} 国债20日涨跌幅</h2></div><ObservationChart rows={rows} field="bond_return20" label="20日涨跌幅" percent zoomGroup={zoomGroup}/></section>
    </>}
    {data?.missing&&<p className="sub">{data.missing}</p>}
  </main>;
}


export function ContextChart({data,cycle=false,flow=false,monthly=false,zoomGroup,lines,height,label,dualAxis=false}) {
  const ref=useRef(),first=cycle||flow||lines?0:data.series.findIndex(p=>p.ratio!=null),rows=cycle||flow||lines?data.series:first<0?[]:data.series.slice(first);
  useChart(ref,chart=>{
    const axis=rows.map(p=>p.trade_date),positions=new Map(axis.map((d,i)=>[d,i]));
    let limit=1;
    let low=Infinity,high=-Infinity;
    if(dualAxis)for(const {key} of lines)for(const p of rows)if(Number.isFinite(p[key])){const value=key==='fund'?p[key]*data.axisRatio+data.axisOffset:p[key];low=Math.min(low,value);high=Math.max(high,value);}
    const padding=(high-low||Math.abs(high)||1)*.06;
    const axes=dualAxis&&lines?.map(({key,color},i)=>{
      const scale=key==='fund'?data.axisRatio:1,offset=key==='fund'?data.axisOffset:0;
      return {type:'value',name:key==='stock'?'沪深300':'债基',scale:true,min:Number.isFinite(low)?(low-padding-offset)/scale:undefined,max:Number.isFinite(high)?(high+padding-offset)/scale:undefined,nameTextStyle:{color},axisLabel:{color,formatter:v=>formatPrice(v)},splitLine:{show:i===0,lineStyle:{color:'#eef0f3'}}};
    });
    const series=lines?lines.map(({key,name,color,dashed},i)=>{
      const last=data.metric?rows.findLast(p=>Number.isFinite(p[key])):null;
      return {name,type:'line',yAxisIndex:dualAxis?i:0,showSymbol:false,connectNulls:false,z:data.metric&&key==='close'?4:2,itemStyle:{color},lineStyle:{width:data.metric&&key==='close'?2.6:2,type:dashed?'dashed':'solid'},data:rows.map(p=>p[key]),endLabel:{show:dualAxis,formatter:key==='stock'?'沪深300':'债基',color,fontSize:12,distance:-6,align:'right',verticalAlign:key==='stock'?'bottom':'top'},markPoint:key==='stock'?{symbol:'circle',symbolSize:6,label:{fontSize:11,color},data:(data.anchors||[]).map((p,j)=>({name:p.trade_date.slice(0,7),coord:[positions.get(p.trade_date),p.stock],label:{position:j%2?'top':'bottom',formatter:'{b}'}}))}:last?{silent:true,symbol:'circle',symbolSize:6,label:{show:false},data:[{coord:[positions.get(last.trade_date),last[key]]}]}:undefined};
    }):cycle?data.cycles.map(c=>{
      const values=Array(axis.length).fill(null);
      for(const p of c.points){const value=cycleScale(p.value);values[positions.get(p.trade_date)]=value;limit=Math.max(limit,Math.ceil(value*4)/4);}
      const color=c.ongoing?'#707b8a':c.direction>0?'#b64d49':'#2b8167',last=c.points.at(-1);
      return {name:c.start,type:'line',showSymbol:false,connectNulls:false,itemStyle:{color},lineStyle:{color,width:2,type:c.ongoing?'dashed':'solid'},areaStyle:{color,opacity:.06},data:values,
        markPoint:{symbol:'circle',symbolSize:6,data:[{coord:[positions.get(last.trade_date),cycleScale(last.value)]}],label:{show:Math.abs(last.value)>=.35||c.ongoing,position:c.direction>0?'top':'bottom',formatter:c.end.slice(0,7)+'\n'+(c.ongoing?'累计 ':'')+format(last.value,true),fontSize:11,color}}};
    }):flow?Object.entries(flowGroups).filter(([key])=>data.funds.some(f=>f.group===key)).map(([key,name])=>({name,type:monthly?'bar':'line',barMaxWidth:14,showSymbol:false,connectNulls:false,itemStyle:{color:key==='institution'?'#a48b59':'#4e7fb9'},lineStyle:{width:2.2},data:rows.map(p=>p[key])})):[{name:'ETF规模占比',type:'line',showSymbol:false,connectNulls:false,itemStyle:{color:'#a48b59'},lineStyle:{width:2},data:rows.map(p=>p.ratio)},{name:'沪深300',type:'line',yAxisIndex:1,showSymbol:false,connectNulls:false,itemStyle:{color:'#4e7fb9'},lineStyle:{width:1.6},data:rows.map(p=>p.index_close)}];
    chart.setOption({animation:false,legend:cycle?undefined:{top:0,left:'center',...(data.metric?{type:'scroll',itemWidth:16,itemHeight:8,itemGap:12,textStyle:{fontSize:12}}:{})},tooltip:{trigger:'axis',confine:true,renderMode:'richText',formatter:params=>{
      const list=params.filter(p=>Number.isFinite(p.value)),day=params[0]?.axisValue;if(!list.length)return '';
      if(lines)return [day,...list.map(p=>`${p.seriesName}：${formatPrice(p.value)}`),...(data.metric&&Number.isFinite(rows[params[0]?.dataIndex]?.value)?[(data.metric==='pb'?'PB':'PE(TTM)')+'：'+format(rows[params[0].dataIndex].value,false,false)]:[])].join('\n');
      if(flow){const row=rows[params[0]?.dataIndex];return [monthly?`${day} · 截至 ${row?.period_end}`:day,...list.map(p=>`${p.seriesName}：${formatPrice(p.value)}亿元`)].join('\n');}
      if(!cycle){const p=rows[params[0]?.dataIndex];return p?[p.trade_date,...[['沪深300',p.index_close,false,''],['占比',p.ratio,true,''],['ETF规模',p.net_assets==null?null:p.net_assets/1e8,false,'亿元'],['沪深300流通市值',p.float_market_cap==null?null:p.float_market_cap/1e8,false,'亿元']].filter(([,v])=>Number.isFinite(v)).map(([name,v,percent,unit])=>name+'：'+(name==='沪深300'?formatPrice(v):format(v,percent))+unit)].join('\n'):'';}
      return [day,...list.map(p=>{const c=data.cycles[p.seriesIndex];return `${c.direction>0?'上涨':'下跌'} ${format(cycleValue(p.value),true)}\n阶段 ${c.start}—${c.end} · ${Math.round((Date.parse(c.end)-Date.parse(c.start))/86400000)}天${c.ongoing?'（进行中）':'；结束确认 '+c.confirmed_on}`;})].join('\n');
    }},grid:{left:68,right:dualAxis?68:cycle||flow||lines?24:70,top:dualAxis?60:40,bottom:58},xAxis:{type:'category',boundaryGap:monthly,data:axis,axisLabel:{hideOverlap:true,...(cycle||data.metric?{showMinLabel:true,showMaxLabel:true}:{})}},
    yAxis:axes|| (lines?{type:'value',scale:true,...(data.metric?{min:({min,max})=>min-(max-min||Math.abs(max)||1)*.06,max:({min,max})=>max+(max-min||Math.abs(max)||1)*.06}:{}),axisLabel:{...(data.metric?{hideOverlap:true,showMinLabel:false,showMaxLabel:false}:{}),formatter:v=>formatPrice(data.metric?Math.round(v):v)},splitLine:{lineStyle:{color:'#eef0f3'}}}:cycle?{type:'value',min:-limit,max:limit,interval:.25,axisLabel:{formatter:v=>format(cycleValue(v),true)},splitLine:{lineStyle:{color:'#eef0f3'}}}:flow?{type:'value',splitLine:{lineStyle:{color:'#eef0f3'}}}:[{type:'value',scale:true,axisLabel:{formatter:v=>format(v,true)},splitLine:{lineStyle:{color:'#eef0f3'}}},{type:'value',scale:true,axisLabel:{formatter:v=>formatPrice(v)},splitLine:{show:false}}]),dataZoom:[{type:'inside',start:0,end:100},{type:'slider',bottom:8,height:18,start:0,end:100}],series},true);
  },[data,cycle,flow,monthly,lines,dualAxis],zoomGroup);
  return <ChartFrame><div className="observation-chart" style={{height:height??(cycle?460:380)}} ref={ref} role="img" aria-label={label||(cycle?'指数牛熊周期':flow?'ETF累计净申购估算':'沪深300ETF规模占比')}/></ChartFrame>;
}

export function comparisonView(data) {
  const lines=(data?.assets||[]).filter(a=>['stock','fund'].includes(a.key)),source=data?.series||[],valid=v=>Number.isFinite(v)&&v>0;
  const first=source.findIndex(p=>valid(p.stock)&&valid(p.fund)),last=source.findLastIndex(p=>valid(p.stock)&&valid(p.fund));
  if(first<0)return {series:[],lines};
  const series=source.slice(first,last+1).map(p=>({trade_date:p.trade_date,stock:valid(p.stock)?p.stock:null,fund:valid(p.fund)?p.fund:null}));
  const months=new Set(['2014-06','2019-01','2024-02','2024-09']),lows=new Map();
  for(const p of series){const month=p.trade_date.slice(0,7);if(months.has(month)&&valid(p.stock)&&valid(p.fund)&&(!lows.has(month)||p.stock<lows.get(month).stock))lows.set(month,p);}
  const anchors=[...lows.values()],mean=key=>anchors.reduce((sum,p)=>sum+p[key],0)/(anchors.length||1),x=mean('fund'),y=mean('stock');
  const variance=anchors.reduce((sum,p)=>sum+(p.fund-x)**2,0),slope=variance?anchors.reduce((sum,p)=>sum+(p.fund-x)*(p.stock-y),0)/variance:0;
  const axisRatio=slope>0?slope:series[0].stock/series[0].fund,axisOffset=slope>0?y-axisRatio*x:0;
  return {series,lines,start:series[0].trade_date,end:series.at(-1).trade_date,anchors:slope>0?anchors:[],axisRatio,axisOffset};
}

export function ComparisonObserve() {
  const [savedEnd,saveEnd]=usePreference('observe.end',''),[end,setEnd]=useState(()=>new URLSearchParams(location.search).get('end')??savedEnd),[revision,refresh]=useState(0);
  useEffect(()=>{saveEnd(end);const q=new URLSearchParams(location.search);end?q.set('end',end):q.delete('end');history.replaceState({},'',location.pathname+'?'+q);},[end]);
  const {data,error,busy}=useResource('/api/futures/v1/observation/context?view=compare'+(end?'&end='+end:''),revision);
  const result=useMemo(()=>comparisonView(data),[data]);
  return <main className="board-content observation-page" aria-busy={busy}>
    <div className="observation-heading"><h1>沪深300与债基</h1></div>
    <div className="filters observation-controls"><input aria-label="观察日" type="date" value={end||result.end||''} onChange={e=>setEnd(e.target.value)}/><RefreshButton busy={busy} onClick={()=>refresh(revision+1)}/></div>
    {error?<p role="alert">{error}</p>:!data?<p role="status">读取股债数据…</p>:!result.series.length?<p>{data.missing||'暂无共同有效区间'}</p>:<section><ContextChart data={result} lines={result.lines} dualAxis height={420} label="沪深300全收益与中证非纯债基走势"/></section>}
  </main>;
}

export function valuationView(data) {
  const v=data?.valuation;if(!v?.latest)return null;
  const values=new Map(v.series.map(p=>[p.trade_date,p.value]));
  const first=data.series.findIndex(p=>values.has(p.trade_date));
  return {...v,series:(first<0?[]:data.series.slice(first)).map(p=>{
    const value=values.get(p.trade_date)??null,scale=value==null?null:p.close/value;
    return {...p,value,lower:scale!=null&&v.lower!=null?scale*v.lower:null,deep:scale!=null&&v.deep!=null?scale*v.deep:null,upper:scale!=null&&v.upper!=null?scale*v.upper:null};
  })};
}

function CycleObserve() {
  const [index,setIndex]=usePreference('observe.index','000300.SH'),[savedEnd,saveEnd]=usePreference('observe.end',''),[revision,refresh]=useState(0);
  const [years,setYears]=usePreference('observe.valuation.years',10);
  const [end,setEnd]=useState(()=>new URLSearchParams(location.search).get('end')??savedEnd);
  useEffect(()=>saveEnd(end),[end]);
  const params=new URLSearchParams({index,valuation_years:years,...(end?{end}:{})});
  const {data,error,busy}=useResource('/api/futures/v1/observation/context?view=cycle&'+params,revision,'cycle');
  const valuation=useMemo(()=>valuationView(data),[data]);
  const zoomGroup=useMemo(()=>Object.assign(new Set(),{byDate:true}),[index,end]);
  useEffect(()=>{const q=new URLSearchParams(location.search);end?q.set('end',end):q.delete('end');history.replaceState({},'',location.pathname+'?'+q);},[end]);
  return <main className="board-content observation-page" aria-busy={busy}>
    <div className="observation-heading"><h1>市场周期</h1></div>
    <div className="filters observation-controls">{<select aria-label="观察指数" value={index} onChange={e=>setIndex(e.target.value)}>{Object.entries(indices).map(([code,name])=><option key={code} value={code}>{name}</option>)}</select>}<input aria-label="观察日" type="date" value={end||data?.series?.at(-1)?.trade_date||''} onChange={e=>setEnd(e.target.value)}/><RefreshButton busy={busy} onClick={()=>refresh(revision+1)}/></div>
    {error?<p role="alert">{error}</p>:!data?<p role="status">读取研究数据…</p>:!data.series?.length?<p>{data.missing||'暂无数据'}</p>:<>
      {data.current&&<div className="observation-status"><div><strong>{data.current.direction>0?(data.current.drawdown<0?'回撤中':'阶段高位'):(data.current.rebound>0?'反弹中':'阶段低位')}</strong><span>20%口径：{data.current.direction>0?'上行阶段':'下行阶段'} · {data.current.as_of}</span></div><div><strong>{format(data.current.drawdown,true)}</strong><span>较阶段高点 {data.current.peak_date}</span></div><div><strong>{format(data.current.rebound,true)}</strong><span>较阶段低点 {data.current.low_date}</span></div></div>}
      <section><div className="observation-section-heading"><h2>{indices[data?.index||index]}历史周期</h2><span className="observation-event-count">上涨对数压缩、下跌百分比</span></div><ContextChart data={data} cycle zoomGroup={zoomGroup}/></section>
      {data.valuation?.latest?<section><div className="filters observation-controls observation-section-heading"><h2>{indices[data.index]}估值参考</h2><select aria-label="估值统计样本" value={years} onChange={e=>setYears(Number(e.target.value))}><option value={10}>近10年样本</option><option value={0}>全历史样本</option></select></div>
        <div className="observation-section-heading valuation-metrics"><Metric label={data.valuation.metric==='pb'?'PB':'PE(TTM)'} value={format(data.valuation.latest.value,false,false)} hint={data.valuation.percentile==null?undefined:'样本分位 '+format(data.valuation.percentile/100,true)}/><Metric label={'深估'+(data.valuation.metric==='pb'?'PB':'PE')} value={format(data.valuation.deep,false,false)} hint="低点中位数 ×90%（试验口径）。"/><Metric label={'低估'+(data.valuation.metric==='pb'?'PB':'PE')} value={format(data.valuation.lower,false,false)} hint={data.valuation.samples.length+'个30%确认低点中位数 ×110%（回溯）。'}/><Metric label={'高估'+(data.valuation.metric==='pb'?'PB':'PE')} value={format(data.valuation.upper,false,false)} hint={data.valuation.history_start+'—'+data.valuation.history_end+' · '+data.valuation.quantile*100+'%分位'}/></div>
        <ContextChart data={valuation} lines={valuationLines} label="指数点位与估值参考" height={460} zoomGroup={zoomGroup}/></section>:<p className="sub">暂无估值数据</p>}
    </>}
  </main>;
}

export function fundMonthlyView(data) {
  if(!data)return data;
  const months=new Map();
  for(const p of data.series){
    const month=p.trade_date.slice(0,7),row=months.get(month)||{trade_date:month,individual:0,institution:0};
    row.period_end=p.trade_date;
    for(const key of Object.keys(flowGroups)){const value=p[key+'_daily'];row[key]=row[key]!=null&&Number.isFinite(value)?row[key]+value:null;}
    months.set(month,row);
  }
  return {...data,series:[...months.values()]};
}

export function fundsView(data,allocation) {
  const monthly=fundMonthlyView(data),keys=Object.keys(flowGroups);
  const first=(source,fields)=>source?.series.find(p=>fields.some(key=>Number.isFinite(p[key])))?.trade_date.slice(0,7);
  const starts=[first(data,keys),first(monthly,keys),first(allocation,['ratio'])],start=starts.every(Boolean)?starts.sort().at(-1):'';
  const trim=source=>source&&start?{...source,series:source.series.filter(p=>p.trade_date.slice(0,7)>=start)}:source;
  return {flow:trim(data),monthly:trim(monthly),allocation:trim(allocation)};
}

function FundsObserve() {
  const [savedEnd,saveEnd]=usePreference('observe.end',''),[end,setEnd]=useState(()=>new URLSearchParams(location.search).get('end')??savedEnd),[revision,refresh]=useState(0);
  const {data,error,busy}=useResource('/api/futures/v1/observation/context?view=flow'+(end?'&end='+end:''),revision,'flow');
  const {data:allocation,error:allocationError,busy:allocationBusy}=useResource('/api/futures/v1/observation/context?view=allocation'+(end?'&end='+end:''),revision,'allocation');
  const {flow,monthly,allocation:scale}=useMemo(()=>fundsView(data,allocation),[data,allocation]),last=flow?.series?.at(-1),ratio=scale?.series?.findLast(p=>Number.isFinite(p.ratio));
  useEffect(()=>{saveEnd(end);const q=new URLSearchParams(location.search);q.delete('fund_view');end?q.set('end',end):q.delete('end');history.replaceState({},'',location.pathname+'?'+q);},[end]);
  return <main className="board-content observation-page" aria-busy={busy||allocationBusy}>
    <div className="observation-heading"><h1>ETF资金</h1></div>
    <div className="filters observation-controls"><input aria-label="观察日" type="date" value={end||last?.trade_date||ratio?.trade_date||''} onChange={e=>setEnd(e.target.value)}/><RefreshButton busy={busy||allocationBusy} onClick={()=>refresh(revision+1)}/></div>
    {allocationError?<p role="alert">{allocationError}</p>:!allocation?<p role="status">读取规模占比…</p>:!ratio?<p>{allocation.missing||'暂无规模占比数据'}</p>:<section><div className="observation-section-heading"><h2>头部沪深300ETF规模占比</h2><span className="observation-event-count">{ratio.trade_date}</span></div><div className="observation-section-heading"><Metric label="规模占比" value={format(ratio.ratio,true)}/></div><ContextChart data={scale} label="沪深300ETF规模占比"/></section>}
    {error?<p role="alert">{error}</p>:!data?<p role="status">读取资金数据…</p>:!last?<p>{data.missing||'暂无数据'}</p>:<>
      <section><div className="observation-section-heading"><h2>累计净申赎估算 / 亿元{data.meta?.currency_assumed?'（人民币假设）':''}</h2><span className="observation-event-count">已纳入{data.coverage?.included_flow??data.funds.length}只</span></div>
      <div className="observation-section-heading">{Object.entries(flowGroups).filter(([key])=>data.funds.some(f=>f.group===key)).map(([key,name])=><Metric key={key} label={name+'ETF'} value={format(last[key])} hint="自样本起点累计的已观测净申购估算。"/>)}</div>
      <ContextChart data={flow} flow label="ETF累计净申赎估算"/></section>
      <section><div className="observation-section-heading"><h2>月度净申赎 / 亿元</h2><span className="observation-event-count">末月截至 {last.trade_date}</span></div><ContextChart data={monthly} flow monthly label="ETF月度净申赎估算"/></section>
    </>}
  </main>;
}

export default function Observe() {
  const views={cycle:'市场周期',stock:'国债强弱',compare:'股债位置',funds:'ETF资金'},query=new URLSearchParams(location.search);
  const [view,setView]=useState(()=>{const value=query.get('view');return ['allocation','flow'].includes(value)?'funds':views[value]?value:Object.keys(views)[0];});
  useEffect(()=>{const q=new URLSearchParams(location.search);q.set('view',view);history.replaceState({},'',location.pathname+'?'+q);},[view]);
  return <><div className="board-content" style={{paddingBottom:0}}><nav className="board-tabs" aria-label="观察页签">{Object.entries(views).map(([key,label])=><button key={key} aria-pressed={view===key} onClick={()=>setView(key)}>{label}</button>)}</nav></div>{view==='stock'?<StockBondObserve/>:view==='compare'?<ComparisonObserve/>:view==='cycle'?<CycleObserve/>:<FundsObserve/>}</>;
}
