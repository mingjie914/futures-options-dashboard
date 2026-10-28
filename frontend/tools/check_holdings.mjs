import assert from 'node:assert/strict';
import { holdingRows, putCallRatio } from '../src/holdings.js';
const rows = [{code:'C7700', oi:10569, volume:13314}, {code:'P7700', oi:5179, volume:6160}, {code:'new', oi:10, volume:1}];
const old = {rows:[{code:'C7700',oi:10080,volume:12000}, {code:'P7700',oi:5200,volume:6200}]};
const values = holdingRows(rows,old,'snapshot');
assert.equal(values[0].oi_change,489);
assert.equal(values[0].volume,1314);
assert.equal(values[1].oi_change,-21);
assert.equal(values[1].volume,null);
assert.equal(values[2].oi_change,null);
assert.equal(values[2].volume,null);
assert.equal(holdingRows(rows,old,'daily'),rows);
assert.equal(rows[0].volume,13314);
const sample=[{cp:'C',oi:100,volume:2},{cp:'P',oi:50,volume:6},{cp:'C',oi:300,volume:8},{cp:'P',oi:150,volume:14}];
assert.equal(putCallRatio(sample,'oi').ratio,.5);
assert.equal(putCallRatio(sample,'volume').ratio,2);
assert.equal(putCallRatio([{cp:'C',oi:100},{cp:'P',oi:0}],'oi').ratio,0);
assert.equal(putCallRatio([{cp:'C',oi:0},{cp:'P',oi:100}],'oi').ratio,null);
assert.equal(putCallRatio([{cp:'C',oi:100}],'oi').ratio,null);
assert.equal(putCallRatio([],'oi').ratio,null);
assert.equal(putCallRatio(sample,'oi',5).ratio,null);
for(const missing of [null,undefined,NaN,Infinity,-1]) {
  const value=putCallRatio([{cp:'C',oi:100},{cp:'P',oi:missing}],'oi');
  assert.equal(value.ratio,null);
  assert.equal(value.covered,1);
  assert.equal(value.put,null);
}
console.log('Holding interval and pooled PCR calculations passed');
