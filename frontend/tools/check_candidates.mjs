import assert from 'node:assert/strict';
import { buildCandidates, budgetGroups } from '../src/candidates.js';
const base = { month: '202609', cp: 'C', bid: 10, ask: 11, bid_size: 100, ask_size: 100, iv: .25, t: .1 };
const rows = [7400,7500,7600,7700,7800].map(strike => ({...base, strike, code: `C${strike}`}));
const candidates = buildCandidates(rows, '202609', 7590, 7700);
assert.deepEqual(candidates.map(c => c.legs), [
  [{code:'C7600',side:'buy',quantity:1}],
  [{code:'C7500',side:'buy',quantity:1}],
  [{code:'C7600',side:'buy',quantity:1},{code:'C7700',side:'sell',quantity:1}],
]);
assert.equal(buildCandidates(rows, '202610', 7590,7700).length,0);
assert.equal(buildCandidates(rows.map(r=>({...r,iv:null})), '202609',7590,7700).length,0);
assert.equal(buildCandidates(rows.map(r=>({...r,ask:30})), '202609',7590,7700).length,0);
assert.equal(buildCandidates(rows.map(r=>({...r,ask_size:null})), '202609',7590,7700).length,0);
assert.equal(budgetGroups({max_loss:4500}, candidates[2], rows, 20000),4);
assert.equal(budgetGroups({max_loss:25000}, candidates[2], rows, 20000),0);
assert.equal(budgetGroups({max_loss:1000}, candidates[2], rows.map(r=>({...r,ask_size:2})), 20000),2);
const response=await fetch('http://127.0.0.1:8790/api/options/v1/snapshot');
const snapshot=await response.json();
const real=buildCandidates(snapshot.rows,snapshot.months[0].id,snapshot.spot,7700);
assert.equal(real.length,3);
for (const candidate of real) {
  const response=await fetch('http://127.0.0.1:8790/api/options/v1/scenarios',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({snapshot_id:snapshot.snapshot_id,legs:candidate.legs,target:7700,days:1,iv_shift_pp:0})});
  assert.equal(response.status,200);
  const value=await response.json();
  assert.ok(Number.isFinite(value.max_loss));
  assert.ok(Math.abs(value.max_loss-value.cost)<1e-6);
  const groups = budgetGroups(value, candidate, snapshot.rows, 20000);
  if (groups > 0) {
    const scaledResponse = await fetch('http://127.0.0.1:8790/api/options/v1/scenarios', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({snapshot_id:snapshot.snapshot_id,legs:candidate.legs.map(leg=>({...leg,quantity:leg.quantity*groups})),target:7700,days:1,iv_shift_pp:0})});
    assert.equal(scaledResponse.status,200);
    const scaled = await scaledResponse.json();
    assert.ok(Math.abs(scaled.pnl-value.pnl*groups)<1e-6);
    assert.ok(Math.abs(scaled.max_loss-value.max_loss*groups)<1e-6);
    assert.ok(scaled.max_loss<=20000);
  }
  console.log(candidate.name,JSON.stringify({cost:value.cost,max_loss:value.max_loss,pnl:value.pnl}));
}
console.log('Candidate selection and all 3 actual API valuations passed');
