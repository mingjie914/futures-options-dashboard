import test from 'node:test';
import assert from 'node:assert/strict';
import {applyLivePacket,liveOnline,openLive,optionRows} from '../src/intraday-live.js';

test('live snapshots replace; deltas merge absolute values and preserve null/removal semantics',()=>{
  const state={quotes:new Map([['OLD',{last:1}]]),epoch:null,version:-1};
  const packet=(type,version,quotes={},epoch='a')=>({type,epoch,version,quotes});
  applyLivePacket(state,packet('snapshot',1,{A:{last:100,prev_close:90},B:{last:20}}));
  assert.equal(state.quotes.has('OLD'),false);
  const first=state.quotes;
  applyLivePacket(state,packet('delta',2,{A:{last:102},B:null}));
  assert.notEqual(state.quotes,first);
  assert.deepEqual(first.get('A'),{last:100,prev_close:90});
  assert.deepEqual(state.quotes.get('A'),{last:102,prev_close:90});
  assert.equal(state.quotes.has('B'),false);
  applyLivePacket(state,packet('delta',3,{A:{last:null}}));
  assert.equal(state.quotes.get('A').last,null);
  assert.equal(applyLivePacket(state,packet('delta',2,{A:{last:999}})),false);
  assert.equal(applyLivePacket(state,packet('delta',10,{A:{last:999}},'old')),false);
  applyLivePacket(state,packet('snapshot',0,{B:{last:5}},'b'));
  assert.deepEqual([...state.quotes],[['B',{last:5}]]);
  const health={source_mode:'wind',source_status:'ok',receiver_age_seconds:10};
  const retained=state.quotes;
  applyLivePacket(state,{...packet('status',0,{},'b'),health});
  assert.equal(state.quotes,retained);
  applyLivePacket(state,packet('delta',1,{},'b'));
  assert.equal(state.quotes,retained);
  Object.assign(state,{connection:'connected',received:1000});
  assert.equal(liveOnline(state,35000),true);
  assert.equal(liveOnline(state,36000),false);
  state.connection='disconnected';assert.equal(liveOnline(state,1000),false);
  state.connection='connected';health.source_mode='demo';assert.equal(liveOnline(state,1000),false);
});

test('option curves align source calls/puts by strike without filling missing quotes',()=>{
  const catalog={codes:['P2','C1','P1','F'],metadata:{P2:{kind:'option',cp:'P',strike:200},C1:{kind:'option',cp:'C',strike:100},P1:{kind:'option',cp:'P',strike:100},F:{kind:'future'}}};
  const rows=optionRows(catalog,new Map([['C1',{iv:0.35,oi:0}],['P2',{iv:null,oi:20}]]));
  assert.deepEqual(rows.map(r=>r.strike),[100,200]);
  assert.equal(rows[0].C.iv,0.35);assert.equal(rows[0].C.oi,0);
  assert.equal(rows[0].P.iv,undefined);assert.equal(rows[1].P.iv,null);assert.equal(rows[1].C,undefined);
});

function setup(t,bootstrap=null) {
  const originals={fetch:globalThis.fetch,WebSocket:globalThis.WebSocket,setTimeout:globalThis.setTimeout,clearTimeout:globalThis.clearTimeout};
  t.after(()=>Object.assign(globalThis,originals));
  const sockets=[],requests=[],timers=new Map(),states=[];
  globalThis.fetch=async url=>{requests.push(url);return{ok:!!bootstrap,json:async()=>bootstrap};};
  globalThis.setTimeout=(fn,delay)=>{timers.set(fn,delay);return fn;};globalThis.clearTimeout=id=>timers.delete(id);
  globalThis.WebSocket=class {constructor(url){this.url=url;this.readyState=0;this.sent=[];sockets.push(this);}send(raw){this.sent.push(JSON.parse(raw));}close(){this.closed=true;}};
  return {sockets,requests,timers,states};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const catalog={codes:['I','F','O'],revision:'one',metadata:{I:{kind:'index'},F:{kind:'future',underlying:'I'},O:{kind:'option',underlying:'I'}}};

test('first subscription needs no directory HTTP; one socket changes kinds, reconnects and disposes',async t=>{
  const {sockets,requests,timers,states}=setup(t);
  const live=openLive('http://127.0.0.1:18808','basis',state=>states.push(state));
  const ws=sockets[0];ws.readyState=1;ws.onopen();
  assert.deepEqual(ws.sent[0],{type:'subscribe',kinds:['index','future'],fields:['last','prev_close']});
  const message=p=>ws.onmessage({data:JSON.stringify(p)});
  message({type:'snapshot',epoch:'a',version:1,catalog,quotes:{I:{last:10}},health:{source_mode:'wind'}});
  const lastQuotes=states.at(-1).quotes;
  message({type:'status',epoch:'a',version:1,health:{source_mode:'wind',source_status:'ok'}});
  assert.equal(states.at(-1).quotes,lastQuotes);
  message({type:'delta',epoch:'a',version:2,quotes:{I:{last:11}}});
  assert.equal(lastQuotes.get('I').last,10);assert.equal(states.at(-1).quotes.get('I').last,11);
  live.select('options');assert.deepEqual(ws.sent.at(-1).kinds,['index','option']);assert.equal(sockets.length,1);
  assert.deepEqual(ws.sent.at(-1).fields,['last','prev_close','bid','ask','iv','oi']);
  assert.deepEqual(states.at(-1).catalog,catalog);
  const retained=states.at(-1).quotes;ws.onclose({code:1006});assert.equal(states.at(-1).quotes,retained);
  const retry=timers.keys().next().value;timers.delete(retry);retry();
  sockets[1].readyState=1;sockets[1].onopen();assert.deepEqual(sockets[1].sent[0].kinds,['index','option']);
  await flush();assert.deepEqual(requests,['/api/intraday/v1/bootstrap?view=basis']);
  live.close();assert.equal(sockets[1].closed,true);assert.equal(timers.size,0);
});

test('warm RAM bootstrap displays before remote handshake; newer stream supersedes it',async t=>{
  const cached={type:'snapshot',epoch:'a',version:10,catalog,quotes:{I:{last:10,acquired_at:'original'}},health:{source_mode:'wind'},connection:'connected'};
  const {sockets,states}=setup(t,cached);
  const live=openLive('http://127.0.0.1:18808','basis',s=>states.push(s));await flush();
  assert.equal(sockets[0].readyState,0);assert.equal(states.at(-1).quotes.get('I').last,10);
  assert.equal(states.at(-1).quotes.get('I').acquired_at,'original');
  sockets[0].onmessage({data:JSON.stringify({...cached,version:11,quotes:{I:{last:12}}})});
  assert.equal(states.at(-1).quotes.get('I').last,12);live.close();
});

test('late bootstrap cannot overwrite live data or publish after disposal',async t=>{
  const {sockets,states}=setup(t);let respond;
  globalThis.fetch=()=>new Promise(resolve=>{respond=resolve;});
  const cached={type:'snapshot',epoch:'a',version:1,catalog,quotes:{I:{last:1}}};
  const live=openLive('http://127.0.0.1:18808','basis',s=>states.push(s));
  sockets[0].onmessage({data:JSON.stringify({...cached,version:2,quotes:{I:{last:2}}})});
  respond({ok:true,json:async()=>cached});await flush();assert.equal(states.at(-1).quotes.get('I').last,2);
  live.close();assert.equal(sockets[0].closed,true);
  const count=states.length;sockets[0].onclose?.({code:1006});await flush();assert.equal(states.length,count);
});
