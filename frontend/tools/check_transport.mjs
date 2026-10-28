import test from 'node:test';
import assert from 'node:assert/strict';
import {applyDelta, conditionalFetch} from '../src/transport.js';

test('conditional requests reconstruct corrections, deletions and empty 304', async () => {
  const originalFetch = globalThis.fetch, received = [];
  const original = {rows:[{id:1,value:10},{id:2,value:20}],old:true};
  const replies = [
    new Response(JSON.stringify(original), {headers:{etag:'W/"1"'}}),
    new Response(JSON.stringify({base:'W/"1"',version:'W/"2"',ops:[['set',['rows',0,'value'],null],['remove',['rows',1]],['remove',['old']]]}),
      {status:226,headers:{etag:'W/"2"',im:'dashboard-json-delta','delta-base':'W/"1"'}}),
    new Response(null, {status:304,headers:{etag:'W/"2"'}}),
  ];
  globalThis.fetch = async (url, options) => {received.push(options.headers);return replies.shift();};
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
  globalThis.fetch = async (url, options) => {
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
