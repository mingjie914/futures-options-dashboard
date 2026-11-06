import test from 'node:test';
import assert from 'node:assert/strict';
import {applyDelta, conditionalFetch, createConditionalFetch, warmNavigation} from '../src/transport.js';

test('conditional requests reconstruct corrections, deletions and empty 304', async () => {
  const originalFetch = globalThis.fetch, received = [];
  const original = {rows:[{id:1,value:10},{id:2,value:20}],old:true};
  const replies = [
    new Response(JSON.stringify(original), {headers:{etag:'W/"1"'}}),
    new Response(JSON.stringify({base:'W/"1"',version:'W/"2"',ops:[['set',['rows',0,'value'],null],['remove',['rows',1]],['remove',['old']]]}),
      {status:226,headers:{etag:'W/"2"',im:'dashboard-json-delta','delta-base':'W/"1"'}}),
    new Response(null, {status:304,headers:{etag:'W/"2"'}}),
  ];
  globalThis.fetch = async (_url, options) => {received.push(options.headers);return replies.shift();};
  try {
    assert.deepEqual(await (await conditionalFetch('/test/delta')).json(), original);
    const expected = {rows:[{id:1,value:null}]};
    assert.deepEqual(await (await conditionalFetch('/test/delta')).json(), expected);
    assert.deepEqual(await (await conditionalFetch('/test/delta')).json(), expected);
    assert.equal(received[1].get('If-None-Match'),'W/"1"');
    assert.equal(received[2].get('If-None-Match'),'W/"2"');
  } finally {globalThis.fetch = originalFetch;}
});

test('invalid or lost delta base retries once without conditional headers', async () => {
  const originalFetch = globalThis.fetch, received = [];
  globalThis.fetch = async (_url, options) => {
    received.push(options.headers);
    return received.length === 1 ? new Response('{}',{status:226}) : new Response('{"recovered":true}',{headers:{etag:'W/"new"'}});
  };
  try {
    assert.deepEqual(await (await conditionalFetch('/test/recovery')).json(),{recovered:true});
    assert.equal(received.length,2);
    assert.equal(received[1].has('If-None-Match'),false);
    assert.equal(received[1].has('A-IM'),false);
    globalThis.fetch = async () => new Response(null,{status:304});
    await assert.rejects(conditionalFetch('/test/missing'),/Missing response body/);
  } finally {globalThis.fetch = originalFetch;}
});

test('array append and root replacement preserve values and reject unsafe paths', () => {
  assert.deepEqual(applyDelta([1],[['set',[1],null]]),[1,null]);
  assert.equal(applyDelta({},[['set',[],0]]),0);
  assert.throws(()=>applyDelta({},[['set',['__proto__','polluted'],true]]),/Invalid delta path/);
  assert.throws(()=>applyDelta({},[['remove',['missing']]]),/Invalid delta operation/);
});


test('cold starts revalidate HTTP bodies; delta bases and forced recovery bypass HTTP storage', async () => {
  const originalFetch = globalThis.fetch, requests = [];
  const body = {rows:[1,2]}, tag = 'W/"v1"';
  globalThis.fetch = async (_, init) => {
    requests.push(init);
    return init.headers.has('A-IM') ? new Response(null,{status:304,headers:{etag:tag}})
      : new Response(JSON.stringify(body),{headers:{etag:tag}});
  };
  try {
    const firstPage = createConditionalFetch();
    assert.deepEqual(await (await firstPage('/reload')).json(),body);
    assert.deepEqual(await (await firstPage('/reload')).json(),body);
    // A new page has no JS baseline. Native HTTP revalidation supplies its full 200 body.
    assert.deepEqual(await (await createConditionalFetch()('/reload')).json(),body);
    assert.deepEqual(requests.map(r=>r.cache),['no-cache','no-store','no-cache']);
    assert.equal(requests[2].headers.has('If-None-Match'),false);
    assert.equal(requests[2].headers.has('A-IM'),false);
    await firstPage('/reload',{},true);
    assert.equal(requests.at(-1).cache,'no-store');
    assert.equal(requests.at(-1).headers.has('If-None-Match'),false);
    assert.equal(requests.at(-1).headers.has('A-IM'),false);
    // Rollbacks are ordinary version changes, not monotonic timestamp comparisons.
    globalThis.fetch = async()=>new Response('{"rows":[0]}',{headers:{etag:'W/"older"'}});
    assert.deepEqual(await (await firstPage('/reload')).json(),{rows:[0]});
  } finally {globalThis.fetch=originalFetch;}
});

test('canonical GET singleflight gives independent bodies and isolates caller cancellation',async t=>{
  const original=globalThis.fetch;t.after(()=>globalThis.fetch=original);
  const calls=[];let respond;
  globalThis.fetch=(url,options)=>{calls.push({url,options});return new Promise(resolve=>respond=resolve);};
  const request=createConditionalFetch(),a=new AbortController();
  const first=request('/api/rows?b=2&a=1',{signal:a.signal}),rejected=assert.rejects(first,{name:'AbortError'});
  const second=request('/api/rows?a=1&b=2'),third=request('/api/rows?b=2&a=1');
  assert.equal(calls.length,1);a.abort();await rejected;assert.equal(calls[0].options.signal.aborted,false);
  respond(new Response('{"rows":[1]}',{headers:{etag:'W/"one"'}}));
  const [b,c]=await Promise.all([second,third]);assert.notEqual(b,c);
  assert.deepEqual(await b.json(),{rows:[1]});assert.deepEqual(await c.json(),{rows:[1]});
  const again=request('/api/rows?a=1&b=2');assert.equal(calls[1].options.headers.get('If-None-Match'),'W/"one"');
  respond(new Response(null,{status:304}));assert.deepEqual(await (await again).json(),{rows:[1]});
});

test('all callers leaving cancels their shared request; new callers and distinct headers stay separate',async t=>{
  const original=globalThis.fetch;t.after(()=>globalThis.fetch=original);
  const calls=[];
  globalThis.fetch=(_url,options)=>new Promise((resolve,reject)=>{calls.push({options,resolve});options.signal.addEventListener('abort',()=>reject(options.signal.reason),{once:true});});
  const request=createConditionalFetch(),a=new AbortController(),b=new AbortController();
  const x=request('/api/rows',{signal:a.signal}),y=request('/api/rows',{signal:b.signal});
  const failures=[assert.rejects(x,{name:'AbortError'}),assert.rejects(y,{name:'AbortError'})];
  a.abort();assert.equal(calls[0].options.signal.aborted,false);b.abort();assert.equal(calls[0].options.signal.aborted,true);await Promise.all(failures);
  const next=request('/api/rows'),other=request('/api/rows',{headers:{Accept:'application/custom+json'}});
  assert.equal(calls.length,3);calls[1].resolve(new Response('{}'));calls[2].resolve(new Response('{}'));await Promise.all([next,other]);
});

test('idle warming is ordered, bounded, same-origin and yields to foreground requests',async t=>{
  const timers=new Map(),idles=new Map(),requests=[],listeners=new Map();let clock=0,pageBusy=true;
  const env={location:{href:'https://site.example/intraday/',origin:'https://site.example'},navigator:{connection:{effectiveType:'4g'}},
    document:{hidden:false,readyState:'complete',querySelector:()=>pageBusy,addEventListener:(k,v)=>listeners.set(k,v),removeEventListener:k=>listeners.delete(k)},
    setTimeout:fn=>{timers.set(++clock,fn);return clock;},clearTimeout:key=>timers.delete(key),requestIdleCallback:fn=>{idles.set(++clock,fn);return clock;},cancelIdleCallback:key=>idles.delete(key),
    fetch:(url,options)=>new Promise((resolve,reject)=>{requests.push({url,options,resolve});options.signal.addEventListener('abort',()=>reject(options.signal.reason),{once:true});})};
  const kick=async()=>{const next=timers.keys().next().value,fn=timers.get(next);timers.delete(next);fn();const idle=idles.keys().next().value,run=idles.get(idle);idles.delete(idle);if(run)run();await new Promise(r=>setImmediate(r));};
  const stop=warmNavigation(['/peer.js','/upper.js','/','/peer.js','https://other.example/x.js'],env);
  assert.equal(requests.length,0);await kick();assert.equal(requests.length,0);pageBusy=false;
  await kick();assert.equal(requests[0].url,'https://site.example/peer.js');assert.equal(requests[0].options.priority,'low');
  const original=globalThis.fetch;t.after(()=>globalThis.fetch=original);globalThis.fetch=async()=>new Response('{}');
  await createConditionalFetch()('/api/foreground');await new Promise(r=>setImmediate(r));assert.equal(requests[0].options.signal.aborted,true);
  await kick();assert.equal(requests[1].url,'https://site.example/peer.js');requests[1].resolve(new Response('code'));await new Promise(r=>setImmediate(r));
  await kick();assert.equal(requests[2].url,'https://site.example/upper.js');requests[2].resolve(new Response('code'));await new Promise(r=>setImmediate(r));
  env.document.hidden=true;await kick();assert.equal(requests.length,3);env.document.hidden=false;
  await kick();assert.equal(requests[3].url,'https://site.example/');assert.equal(requests[3].options.cache,'no-cache');
  stop();assert.equal(requests[3].options.signal.aborted,true);assert.equal(timers.size,0);assert.equal(listeners.size,0);
  await new Promise(r=>setImmediate(r));
  warmNavigation(['/peer.js','/upper.js'],env)();assert.equal(timers.size,0);
  env.navigator.connection.saveData=true;warmNavigation(['/skip.js'],env)();assert.equal(timers.size,0);
});
