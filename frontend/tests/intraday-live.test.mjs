import test from 'node:test';
import assert from 'node:assert/strict';
import {applyLivePacket,basisRows,intradayRows,liveOnline,mergeIntraday,openLive,optionRows,optionSeries,optionSmile} from '../src/intraday-live.js';

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

test('option months come from the real directory; equal strikes from other series never mix',()=>{
  const codes=['MO2612-C-6000.CFE','IO2611-C-6000.CFE','MO2611-P-6000.CFE','MO2611-C-6000.CFE','IM2611.CFE'];
  const catalog={codes,metadata:Object.fromEntries(codes.map(code=>[code,{kind:code.startsWith('IM')?'future':'option',cp:code.includes('-P-')?'P':'C',strike:6000}]))};
  const quotes=new Map(codes.map((code,i)=>[code,{iv:i/10,oi:i}]));
  assert.deepEqual(optionSeries(catalog),['IO2611','MO2611','MO2612']);
  const rows=optionRows(catalog,quotes,'MO2611');
  assert.equal(rows.length,1);assert.equal(rows[0].C.code,'MO2611-C-6000.CFE');assert.equal(rows[0].P.code,'MO2611-P-6000.CFE');
  assert.equal(optionRows(catalog,quotes,'HO2611').length,0);
});

test('one smile uses OTM puts/calls and the nearest paired ATM mean without filling invalid IV',()=>{
  const rows=[{strike:90,C:{iv:.8},P:{iv:.3}},{strike:100,C:{iv:.36},P:{iv:.24}},{strike:110,C:{iv:.35},P:{iv:.9}}];
  assert.deepEqual(optionSmile(rows,101),[[90,.3],[100,.3],[110,.35]]);
  assert.deepEqual(optionSmile(rows,105),[[90,.3],[100,.3],[110,.35]]);
  assert.ok(optionSmile(rows,null).every(([,iv])=>iv===null));
  rows[1].P.iv=0;rows[2].C.iv=null;
  assert.deepEqual(optionSmile(rows,101),[[90,.3],[100,null],[110,null]]);
  assert.equal(rows[0].C.iv,.8);
});

test('live basis matches the reference units and keeps unavailable OI/multiplier/expiry blank',()=>{
  const metadata={IH2611:{kind:'future',underlying:'I',last_trade_date:'2026-11-20',multiplier:300},IH2612:{kind:'future',underlying:'I',last_trade_date:'2026-12-18'}};
  const quotes=new Map([['I',{last:2762.54}],['IH2611',{last:2744.2,acquired_at:'2026-11-02T15:00:00+08:00',oi:0}],['IH2612',{last:2700}]]);
  const [r,missing]=basisRows('I',metadata,quotes);
  assert.ok(Math.abs(r.basis-18.34)<1e-8);assert.equal(r.days,18);
  assert.ok(Math.abs(r.absolute-18.34/2762.54*100)<1e-8);
  assert.ok(Math.abs(r.annual-18.34/2762.54*365/18*100)<1e-8);
  assert.ok(Math.abs(r.daily-305.6666666667)<1e-8);assert.equal(r.oi,0);
  assert.equal(missing.daily,null);assert.equal(missing.annual,null);assert.equal(missing.days,null);assert.equal(missing.oi,undefined);
  quotes.set('IH2611',{last:2800,acquired_at:'2026-11-20T15:00:00+08:00'});
  const expired=basisRows('I',metadata,quotes)[0];assert.ok(expired.absolute<0);assert.equal(expired.annual,null);assert.equal(expired.daily,null);
});

test('intraday snapshots/upserts preserve source observations, minute gaps, zeros and selection boundaries',()=>{
  const initial={series:'MO2611',trade_date:'2026-11-03',future_code:'IM2611.CFE',revision:1,time_basis:'observation',points:[{time:'2026-11-03T09:30:00+08:00',future_last:6000,atm_iv:0.3,strike:6000}]};
  const next={...initial,revision:2,points:[{time:'2026-11-03T09:32:00+08:00',future_last:0,atm_iv:null,strike:6000}]};
  const merged=mergeIntraday(initial,next);
  assert.equal(initial.points.length,1);assert.equal(merged.points.length,2);
  assert.equal(intradayRows(merged).length,242);assert.equal(intradayRows(merged).at(-1).time,'15:00');assert.ok(!intradayRows(merged).some(p=>p.time.startsWith('12:')));
  assert.deepEqual(intradayRows(merged).slice(0,3).map(p=>[p.time,p.future_last,p.atm_iv]),[['09:30',6000,0.3],['09:31',null,null],['09:32',0,null]]);
  const correction={...next,revision:3,points:[{...next.points[0],future_last:6020}]};
  assert.equal(mergeIntraday(merged,correction).points.at(-1).future_last,6020);
  assert.equal(mergeIntraday(merged,merged),merged);
  assert.deepEqual(mergeIntraday(merged,{...next,trade_date:'2026-11-04'}).points,next.points);
  const provenance={...merged,price_time_basis:'wind_bar',iv_time_basis:'work_observation'};
  assert.equal(mergeIntraday(provenance,correction).price_time_basis,'wind_bar');
  assert.equal(mergeIntraday(provenance,{...correction,price_time_basis:null}).price_time_basis,null);
  assert.equal(mergeIntraday(provenance,{...next,trade_date:'2026-11-04'}).price_time_basis,undefined);
  const state={quotes:new Map([['I',{last:10}]]),series:'MO2611',epoch:'a',version:1,health:{receiver_age_seconds:5}};
  const quotes=state.quotes,health=state.health;
  applyLivePacket(state,{type:'intraday',epoch:'a',version:2,intraday:initial});
  assert.equal(state.quotes,quotes);assert.equal(state.health,health);assert.equal(state.intraday,initial);
  applyLivePacket(state,{type:'intraday',epoch:'a',version:3,intraday:{...next,series:'IO2611'}});
  assert.equal(state.intraday,initial);
  applyLivePacket(state,{type:'snapshot',epoch:'b',version:0,quotes:{},intraday:next});
  assert.equal(state.intraday,next);assert.equal(state.quotes.size,0);
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

test('first subscription needs no directory HTTP; one combined socket reconnects and disposes',async t=>{
  const {sockets,requests,timers,states}=setup(t);
  const live=openLive('http://127.0.0.1:18808',state=>states.push(state));
  const ws=sockets[0];ws.readyState=1;ws.onopen();
  assert.deepEqual(ws.sent[0],{type:'subscribe',kinds:['index','future','option'],fields:['last','prev_close','iv','oi'],series:'MO2611'});
  const message=p=>ws.onmessage({data:JSON.stringify(p)});
  message({type:'snapshot',epoch:'a',version:1,catalog,quotes:{I:{last:10}},health:{source_mode:'wind'}});
  const lastQuotes=states.at(-1).quotes;
  message({type:'status',epoch:'a',version:1,health:{source_mode:'wind',source_status:'ok'}});
  assert.equal(states.at(-1).quotes,lastQuotes);
  message({type:'delta',epoch:'a',version:2,quotes:{I:{last:11}}});
  assert.equal(lastQuotes.get('I').last,10);assert.equal(states.at(-1).quotes.get('I').last,11);
  assert.equal(sockets.length,1);
  live.select('MO2612');assert.equal(sockets.length,1);assert.equal(ws.sent.at(-1).series,'MO2612');
  live.select('MO2612');assert.equal(ws.sent.length,2);
  assert.deepEqual(ws.sent.at(-1).fields,['last','prev_close','iv','oi']);
  assert.deepEqual(states.at(-1).catalog,catalog);
  const retained=states.at(-1).quotes;ws.onclose({code:1006});assert.equal(states.at(-1).quotes,retained);
  const retry=timers.keys().next().value;timers.delete(retry);retry();
  sockets[1].readyState=1;sockets[1].onopen();assert.deepEqual(sockets[1].sent[0].kinds,['index','future','option']);
  await flush();assert.deepEqual(requests,['/api/intraday/v1/bootstrap?series=MO2611']);
  live.close();assert.equal(sockets[1].closed,true);assert.equal(timers.size,0);
});

test('warm RAM bootstrap displays before remote handshake; newer stream supersedes it',async t=>{
  const cached={type:'snapshot',epoch:'a',version:10,catalog,quotes:{I:{last:10,acquired_at:'original'}},health:{source_mode:'wind'},connection:'connected'};
  const {sockets,states}=setup(t,cached);
  const live=openLive('http://127.0.0.1:18808',s=>states.push(s));await flush();
  assert.equal(sockets[0].readyState,0);assert.equal(states.at(-1).quotes.get('I').last,10);
  assert.equal(states.at(-1).quotes.get('I').acquired_at,'original');
  sockets[0].onmessage({data:JSON.stringify({...cached,version:11,quotes:{I:{last:12}}})});
  assert.equal(states.at(-1).quotes.get('I').last,12);live.close();
});

test('late bootstrap cannot overwrite live data or publish after disposal',async t=>{
  const {sockets,states}=setup(t);let respond;
  globalThis.fetch=()=>new Promise(resolve=>{respond=resolve;});
  const cached={type:'snapshot',epoch:'a',version:1,catalog,quotes:{I:{last:1}}};
  const live=openLive('http://127.0.0.1:18808',s=>states.push(s));
  sockets[0].onmessage({data:JSON.stringify({...cached,version:2,quotes:{I:{last:2}}})});
  respond({ok:true,json:async()=>cached});await flush();assert.equal(states.at(-1).quotes.get('I').last,2);
  live.close();assert.equal(sockets[0].closed,true);
  const count=states.length;sockets[0].onclose?.({code:1006});await flush();assert.equal(states.length,count);
  sockets[0].onmessage({data:JSON.stringify({...cached,version:99})});assert.equal(states.length,count);
});
