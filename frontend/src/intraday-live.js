// The live receiver sends absolute field values, not increments.
const formats=new Map([0,1,2].map(digits=>[digits,new Intl.NumberFormat('zh-CN',{minimumFractionDigits:digits===1?1:0,maximumFractionDigits:digits})]));
export const formatNumber=(value,digits=2)=>Number.isFinite(value)?(formats.get(digits)||formats.get(2)).format(value):'—';

export function applyLivePacket(state, packet) {
  if (!['snapshot','delta','status','intraday'].includes(packet.type)) return false;
  const snapshot=packet.type==='snapshot';
  if ((!snapshot && packet.epoch!==state.epoch) || (packet.epoch===state.epoch && packet.version<state.version)) return false;
  const entries=Object.entries(packet.quotes||{});
  if(snapshot||entries.length) {
    const quotes=new Map(snapshot?[]:state.quotes);
    for (const [code,quote] of entries) {
      if (quote===null) quotes.delete(code);
      else quotes.set(code,{...quotes.get(code),...quote});
    }
    state.quotes=quotes;
  }
  Object.assign(state,{epoch:packet.epoch,version:packet.version,health:packet.health||state.health});
  if(packet.catalog)state.catalog=packet.catalog;
  if(snapshot)state.intraday=null;
  if(packet.intraday&&(!state.series||packet.intraday.series===state.series))state.intraday=mergeIntraday(state.intraday,packet.intraday,snapshot);
  return true;
}

export function mergeIntraday(current,next,replace=false) {
  if(replace||current?.series!==next.series||current?.trade_date!==next.trade_date)return next;
  if(current.revision===next.revision)return current;
  const points=new Map(current.points.map(p=>[p.time,p]));
  for(const p of next.points)points.set(p.time,p);
  return {...current,...next,points:[...points.values()].sort((a,b)=>a.time.localeCompare(b.time))};
}

const intradayClock=new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
const sessionTimes=[570,780].flatMap(start=>Array.from({length:121},(_,i)=>String(Math.floor((start+i)/60)).padStart(2,'0')+':'+String((start+i)%60).padStart(2,'0')));
export function intradayRows(data) {
  const points=new Map((data?.points||[]).map(p=>[p.time.length>8?intradayClock.format(new Date(p.time)):p.time.slice(0,5),p]));
  return points.size?sessionTimes.map(time=>({future_last:null,atm_iv:null,...points.get(time),time})):[];
}

export function openLive(base, publish,series='MO2611',bootstrapBase='',primaryBase='') {
  const routes=[...new Set([primaryBase,base].filter(Boolean).map(url=>url.replace(/\/$/,'')))];
  let socket,retry,watchdog,wake,route=0,stopped=false,delay=1000,received=0;
  const state={quotes:new Map(),catalog:null,intraday:null,series,health:null,epoch:null,version:-1,connection:'connecting'};
  const emit=()=>publish({...state,received});
  const controller=new AbortController();
  const visible=()=>globalThis.document?.visibilityState!=='hidden';
  const visibility=()=>{clearTimeout(watchdog);if(visible())wake?.();};
  if(routes.length>1)globalThis.document?.addEventListener('visibilitychange',visibility);
  function connect() {
    state.connection='connecting';emit();
    const ws=socket=new WebSocket(routes[route].replace(/^http/,'ws')+'/api/realtime/v1/stream');
    let ready=false,pending;
    const active=()=>!stopped&&socket===ws;
    const arm=ms=>{clearTimeout(watchdog);if(routes.length>1&&visible())watchdog=setTimeout(()=>fail(),ms);};
    function fail(code) {
      if(!active())return;
      socket=null;clearTimeout(watchdog);ws.close();
      if(routes.length>1&&route===0){route=1;connect();return;}
      state.connection=code===1008?'unauthorized':'disconnected';emit();
      if(code!==1008){route=0;retry=setTimeout(connect,delay);delay=Math.min(delay*2,30000);}
    }
    wake=()=>arm(ready?45000:route?12000:3000);wake();
    ws.onopen=()=>{if(active())subscribe();};
    ws.onmessage=event=>{
      if(!active())return;
      let packet;try{packet=JSON.parse(event.data);}catch{return;}
      if(!packet||!Number.isFinite(packet.version)||typeof packet.epoch!=='string'||(!ready&&(packet.type!=='snapshot'||!packet.catalog)))return;
      // Keep a lagging full baseline private until this connection catches up.
      if(!ready){ready=true;if(packet.epoch===state.epoch&&packet.version<state.version)pending={...state,epoch:null,version:-1};}
      const target=pending||state;
      if(target.series!==state.series){target.series=state.series;target.intraday=null;}
      if(!applyLivePacket(target,packet))return;
      if(packet.type!=='intraday')arm(45000);
      if(pending){if(pending.epoch===state.epoch&&pending.version<state.version)return;Object.assign(state,pending);pending=null;controller.abort();}
      if(packet.type==='snapshot'&&state.catalog)controller.abort();
      if(packet.type!=='intraday')received=performance.now();delay=1000;state.connection='connected';emit();
    };
    ws.onerror=()=>fail();
    ws.onclose=event=>fail(event.code);
  }
  function subscribe() {
    if(socket?.readyState===1)socket.send(JSON.stringify({type:'subscribe',kinds:['index','future','option'],fields:['last','prev_close','iv','oi'],series:/^(IO|HO|MO)\d{4}$/.test(series)?series:null}));
  }
  connect();
  fetch(bootstrapBase.replace(/\/$/,'')+'/api/intraday/v1/bootstrap?series='+encodeURIComponent(series),{signal:controller.signal,cache:'no-store'}).then(async response=>{
    if(!response.ok||stopped||state.epoch!==null)return;
    const cached=await response.json();
    if(stopped||state.epoch!==null||cached.type!=='snapshot'||!cached.catalog)return;
    applyLivePacket(state,cached);received=performance.now();state.connection=cached.connection;emit();
  }).catch(()=>{});
  return {select(next){if(next===series)return;state.series=series=next;state.intraday=null;emit();subscribe();},close(){stopped=true;controller.abort();clearTimeout(retry);clearTimeout(watchdog);if(routes.length>1)globalThis.document?.removeEventListener('visibilitychange',visibility);socket?.close();}};
}

export function optionSeries(catalog) {
  return [...new Set((catalog?.codes||[]).filter(code=>catalog.metadata[code]?.kind==='option').map(code=>code.match(/^(IO|HO|MO)\d{4}(?=-)/)?.[0]).filter(Boolean))].sort();
}

export function optionRows(catalog,quotes,series) {
  const rows=new Map();
  for(const code of catalog?.codes||[]) {
    const meta=catalog.metadata[code];
    if(meta?.kind!=='option'||series&&!code.startsWith(series+'-')||!Number.isFinite(meta.strike)||!['C','P'].includes(meta.cp))continue;
    if(!rows.has(meta.strike))rows.set(meta.strike,{strike:meta.strike});
    rows.get(meta.strike)[meta.cp]={code,...quotes.get(code)};
  }
  return [...rows.values()].sort((a,b)=>a.strike-b.strike);
}

export function optionSmile(rows,spot) {
  let atm=null;
  if(Number.isFinite(spot))for(const r of rows)if(r.C&&r.P&&(!atm||Math.abs(r.strike-spot)<Math.abs(atm.strike-spot)))atm=r;
  const valid=v=>Number.isFinite(v)&&v>0;
  return rows.map(r=>{
    const c=r.C?.iv,p=r.P?.iv,iv=!Number.isFinite(spot)?null:r===atm?(valid(c)&&valid(p)?(c+p)/2:null):r.strike<spot?p:c;
    return [r.strike,valid(iv)?iv:null];
  });
}

export function basisRows(code,metadata,quotes) {
  const spot=quotes.get(code)?.last;
  return Object.entries(metadata).filter(([,m])=>m.kind==='future'&&m.underlying===code).sort(([a],[b])=>a.localeCompare(b)).map(([contract,m])=>{
    const q=quotes.get(contract),basis=Number.isFinite(spot)&&Number.isFinite(q?.last)?spot-q.last:null;
    const days=m.last_trade_date&&q?.acquired_at?(Date.parse(m.last_trade_date)-Date.parse(q.acquired_at.slice(0,10)))/86400000:null;
    const absolute=Number.isFinite(basis)&&Number.isFinite(m.multiplier)&&m.multiplier>0?basis*m.multiplier/10000:null;
    return {contract,last:q?.last,basis,days,absolute,annual:days>0&&spot>0&&Number.isFinite(basis)?basis/spot*365/days*100:null,
      daily:days>0&&absolute!==null?absolute*10000/days:null,oi:q?.oi};
  });
}

export function basisComparison(groups,quotes) {
  const books=groups.map(group=>({group,rows:basisRows(group.code,group.metadata,quotes),spot:quotes.get(group.code)}));
  if(groups.length!==4)return {books,months:[],strength:[]};
  const delivery=r=>r.contract.split('.')[0].slice(-4);
  const months=books[0].rows.map(delivery).filter(m=>books.every(b=>b.rows.some(r=>delivery(r)===m))).sort();
  const day=books.reduce((latest,b)=>{const d=b.spot?.acquired_at?.slice(0,10)||'';return d>latest?d:latest;},'');
  const strength=books.map(({rows,spot})=>({product:rows[0]?.contract.slice(0,2),change:spot?.acquired_at?.slice(0,10)===day&&Number.isFinite(spot.last)&&Number.isFinite(spot.prev_close)&&spot.prev_close>0?(spot.last/spot.prev_close-1)*100:null}));
  const base=strength.find(r=>r.product==='IM');
  return {books,strength:strength.map(r=>({...r,difference:Number.isFinite(r.change)&&Number.isFinite(base?.change)?r.change-base.change:null})),months:months.map(month=>{
    const rows=books.map(({group,rows,spot})=>{
      const r=rows.find(r=>delivery(r)===month),meta=group.metadata[r.contract],q=quotes.get(r.contract),valid=spot?.acquired_at?.slice(0,10)===day&&q?.acquired_at?.slice(0,10)===day;
      return {...r,product:r.contract.slice(0,2),name:group.metadata[group.code].name,expiry:meta.last_trade_date,last:valid?r.last:null,basis:valid?r.basis:null,annual:valid?r.annual:null,nominal:valid&&Number.isFinite(r.last)&&Number.isFinite(meta.multiplier)?r.last*meta.multiplier:null};
    });
    const base=rows.find(r=>r.product==='IM');
    return {month,rows:rows.map(r=>{
      const delta=key=>r.expiry===base.expiry&&Number.isFinite(r[key])&&Number.isFinite(base[key])?r[key]-base[key]:null;
      return {...r,difference:delta('annual'),basisDifference:delta('basis'),priceDifference:delta('last')};
    })};
  })};
}

export function liveMatrix(metadata,quotes,index) {
  const asOf=index?quotes.get(index)?.acquired_at?.slice(0,10):Object.keys(metadata).reduce((day,code)=>{const date=quotes.get(code)?.acquired_at?.slice(0,10)||'';return date>day?date:day;},'');
  const contracts=Object.entries(metadata).filter(([,m])=>m.kind==='future').map(([code,m])=>{const q=quotes.get(code);return {symbol:code.split('.')[0],close:q?.acquired_at?.slice(0,10)===asOf?q?.last:null,trade_date:asOf,last_trade_date:m.last_trade_date,status:'wind_recorded'};});
  return {meta:{as_of:asOf},contracts,contract_terms:{as_of:asOf,contracts}};
}

export function liveOnline(state, now) {
  const health=state.health;
  return state.connection==='connected' && health?.source_mode==='wind' && health.source_status==='ok'
    && Number.isFinite(health.receiver_age_seconds) && health.receiver_age_seconds+(now-state.received)/1000<45;
}
