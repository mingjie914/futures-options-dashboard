// The live receiver sends absolute field values, not increments.
const decimal=new Intl.NumberFormat('zh-CN',{maximumFractionDigits:2}),integer=new Intl.NumberFormat('zh-CN',{maximumFractionDigits:0});
export const formatNumber=(value,digits=2)=>Number.isFinite(value)?(digits===0?integer:decimal).format(value):'—';

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
export function intradayRows(data) {
  const points=new Map((data?.points||[]).map(p=>[p.time.length>8?intradayClock.format(new Date(p.time)):p.time.slice(0,5),p]));
  const rows=[];
  if(!points.size)return rows;
  for(let m=570;m<=900;m++) {
    if(m>690&&m<780)continue;
    const time=String(Math.floor(m/60)).padStart(2,'0')+':'+String(m%60).padStart(2,'0');
    rows.push({future_last:null,atm_iv:null,...points.get(time),time});
  }
  return rows;
}

export function openLive(base, publish,series='MO2611') {
  let socket,retry,stopped=false,delay=1000,received=0;
  const state={quotes:new Map(),catalog:null,intraday:null,series,health:null,epoch:null,version:-1,connection:'connecting'};
  const emit=()=>publish({...state,received});
  const controller=new AbortController();
  function connect() {
    state.connection='connecting';emit();
    socket=new WebSocket(base.replace(/^http/,'ws')+'/api/realtime/v1/stream');
    socket.onopen=subscribe;
    socket.onmessage=event=>{
      if(stopped)return;
      const packet=JSON.parse(event.data);
      if(!applyLivePacket(state,packet))return;
      if(packet.type!=='intraday')received=performance.now();delay=1000;state.connection='connected';emit();
    };
    socket.onclose=event=>{if(!stopped){state.connection=event.code===1008?'unauthorized':'disconnected';emit();if(event.code!==1008){clearTimeout(retry);retry=setTimeout(connect,delay);delay=Math.min(delay*2,30000);}}};
  }
  function subscribe() {
    if(socket?.readyState===1)socket.send(JSON.stringify({type:'subscribe',kinds:['index','future','option'],fields:['last','prev_close','iv','oi'],...(/^(IO|HO|MO)\d{4}$/.test(series)?{series}:{})}));
  }
  connect();
  fetch('/api/intraday/v1/bootstrap?series='+encodeURIComponent(series),{signal:controller.signal,cache:'no-store'}).then(async response=>{
    if(!response.ok)return;
    const cached=await response.json();
    if(stopped||state.epoch!==null||cached.type!=='snapshot'||!cached.catalog)return;
    applyLivePacket(state,cached);received=performance.now();state.connection=cached.connection;emit();
  }).catch(()=>{});
  return {select(next){if(next===series)return;state.series=series=next;state.intraday=null;emit();subscribe();},close(){stopped=true;controller.abort();clearTimeout(retry);if(socket){socket.onclose=null;socket.close();}}};
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
  const atm=Number.isFinite(spot)?rows.filter(r=>r.C&&r.P).reduce((best,r)=>!best||Math.abs(r.strike-spot)<Math.abs(best.strike-spot)?r:best,null):null;
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
    const absolute=spot>0&&Number.isFinite(basis)?basis/spot*100:null;
    return {contract,last:q?.last,basis,days,absolute,annual:days>0&&absolute!==null?absolute*365/days:null,
      daily:days>0&&Number.isFinite(basis)&&Number.isFinite(m.multiplier)&&m.multiplier>0?basis*m.multiplier/days:null,oi:q?.oi};
  });
}

export function liveOnline(state, now) {
  const health=state.health;
  return state.connection==='connected' && health?.source_mode==='wind' && health.source_status==='ok'
    && Number.isFinite(health.receiver_age_seconds) && health.receiver_age_seconds+(now-state.received)/1000<45;
}
