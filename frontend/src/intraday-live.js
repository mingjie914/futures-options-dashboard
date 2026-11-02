// The live receiver sends absolute field values, not increments.
export function applyLivePacket(state, packet) {
  if (!['snapshot','delta','status'].includes(packet.type)) return false;
  const snapshot=packet.type==='snapshot';
  if ((!snapshot && packet.epoch!==state.epoch) || (packet.epoch===state.epoch && packet.version<state.version)) return false;
  if (snapshot) state.quotes.clear();
  for (const [code,quote] of Object.entries(packet.quotes||{})) {
    if (quote===null) state.quotes.delete(code);
    else state.quotes.set(code,{...state.quotes.get(code),...quote});
  }
  Object.assign(state,{epoch:packet.epoch,version:packet.version,health:packet.health||state.health});
  return true;
}

export function openLive(base, view, publish) {
  let socket,retry,stopped=false,delay=1000,received=0;
  base+='/api/realtime/v1';
  const state={quotes:new Map(),catalog:null,health:null,epoch:null,version:-1,connection:'connecting'};
  const emit=()=>publish({...state,quotes:new Map(state.quotes),received});
  const controller=new AbortController();
  async function catalog() {
    const response=await fetch(base+'/catalog',{signal:controller.signal,cache:'no-store'});
    if (!response.ok) throw Error('catalog');
    return response.json();
  }
  async function connect() {
    state.connection='connecting';emit();
    try {
      state.catalog=await catalog();
      if(stopped)return;
      socket=new WebSocket(base.replace(/^http/,'ws')+'/stream');
      socket.onopen=subscribe;
      socket.onmessage=event=>{
        const packet=JSON.parse(event.data);
        const resnapshot=packet.type==='snapshot'&&state.epoch!==null;
        if (!applyLivePacket(state,packet)) return;
        received=performance.now();delay=1000;state.connection='connected';emit();
        if(resnapshot)catalog().then(next=>{if(!stopped&&next.revision!==state.catalog.revision){state.catalog=next;subscribe();emit();}}).catch(()=>{});
      };
      socket.onclose=event=>{if(!stopped){state.connection=event.code===1008?'unauthorized':'disconnected';emit();if(event.code!==1008)reconnect();}};
      emit();
    } catch (error) {
      if (!stopped && error.name!=='AbortError') {state.connection='disconnected';emit();reconnect();}
    }
  }
  function subscribe() {
    if(socket?.readyState!==1)return;
    const {codes,metadata}=state.catalog,indices=new Set(codes.filter(code=>metadata[code]?.kind==='index'));
    const selected=view==='basis'?codes.filter(code=>indices.has(code)||metadata[code]?.kind==='future'&&indices.has(metadata[code]?.underlying)):[];
    socket.send(JSON.stringify({type:'subscribe',codes:selected,fields:['last','prev_close']}));
  }
  function reconnect() {retry=setTimeout(()=>{state.epoch=null;state.version=-1;connect();},delay);delay=Math.min(delay*2,30000);}
  connect();
  return {select(next){view=next;subscribe();},close(){stopped=true;controller.abort();clearTimeout(retry);if(socket){socket.onclose=null;socket.close();}}};
}

export function liveOnline(state, now) {
  const health=state.health;
  return state.connection==='connected' && health?.source_mode==='wind' && health.source_status==='ok'
    && Number.isFinite(health.receiver_age_seconds) && health.receiver_age_seconds+(now-state.received)/1000<45;
}
