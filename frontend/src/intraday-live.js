// The live receiver sends absolute field values, not increments.
export function applyLivePacket(state, packet) {
  if (!['snapshot','delta','status'].includes(packet.type)) return false;
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
  return true;
}

export function openLive(base, view, publish) {
  let socket,retry,stopped=false,delay=1000,received=0;
  const state={quotes:new Map(),catalog:null,health:null,epoch:null,version:-1,connection:'connecting'};
  const emit=()=>publish({...state,received});
  const controller=new AbortController();
  function connect() {
    state.connection='connecting';emit();
    socket=new WebSocket(base.replace(/^http/,'ws')+'/api/realtime/v1/stream');
    socket.onopen=subscribe;
    socket.onmessage=event=>{
      const packet=JSON.parse(event.data);
      if(!applyLivePacket(state,packet))return;
      received=performance.now();delay=1000;state.connection='connected';emit();
    };
    socket.onclose=event=>{if(!stopped){state.connection=event.code===1008?'unauthorized':'disconnected';emit();if(event.code!==1008){clearTimeout(retry);retry=setTimeout(connect,delay);delay=Math.min(delay*2,30000);}}};
  }
  function subscribe() {
    if(socket?.readyState===1)socket.send(JSON.stringify({type:'subscribe',kinds:['index',view==='basis'?'future':'option'],fields:view==='basis'?['last','prev_close']:['last','prev_close','bid','ask','iv','oi']}));
  }
  connect();
  const initialView=view;
  fetch('/api/intraday/v1/bootstrap?view='+view,{signal:controller.signal,cache:'no-store'}).then(async response=>{
    if(!response.ok)return;
    const cached=await response.json();
    if(stopped||view!==initialView||state.epoch!==null||cached.type!=='snapshot'||!cached.catalog)return;
    applyLivePacket(state,cached);received=performance.now();state.connection=cached.connection;emit();
  }).catch(()=>{});
  return {select(next){view=next;subscribe();},close(){stopped=true;controller.abort();clearTimeout(retry);if(socket){socket.onclose=null;socket.close();}}};
}

export function optionRows(catalog,quotes) {
  const rows=new Map();
  for(const code of catalog?.codes||[]) {
    const meta=catalog.metadata[code];
    if(meta?.kind!=='option'||!Number.isFinite(meta.strike)||!['C','P'].includes(meta.cp))continue;
    if(!rows.has(meta.strike))rows.set(meta.strike,{strike:meta.strike});
    rows.get(meta.strike)[meta.cp]={code,...quotes.get(code)};
  }
  return [...rows.values()].sort((a,b)=>a.strike-b.strike);
}

export function liveOnline(state, now) {
  const health=state.health;
  return state.connection==='connected' && health?.source_mode==='wind' && health.source_status==='ok'
    && Number.isFinite(health.receiver_age_seconds) && health.receiver_age_seconds+(now-state.received)/1000<45;
}
