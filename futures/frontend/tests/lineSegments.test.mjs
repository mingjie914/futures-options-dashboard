import {observedRows} from '../../../frontend/src/charts.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import {lineSegments, tooltipRows, seasonalSelection, inventoryHistory, holdingLeaders} from '../src/lineSegments.mjs';

test('observed ranges trim only empty edges, keeping zero, interior gaps and original dates',()=>{
  const rows=[null,0,null,3,null].map((value,i)=>({date:i,value}));
  assert.deepEqual(observedRows(rows,['value']),rows.slice(1,4));
  assert.deepEqual(observedRows([{value:null},{value:NaN}],['value']),[]);
  assert.equal(rows.length,5);
});

test('inventory keeps original units, null gaps, leap days and previous publication deltas', () => {
  const points=[{date:'2023-12-29',value:12.5},{date:'2024-01-05',value:0},{date:'2024-02-23',value:null},{date:'2024-02-29',value:9},{date:'2024-03-08',value:11},{date:'2025-01-03',value:80}];
  const result=inventoryHistory(points,'2024-12-31',1);
  assert.deepEqual(result.rows.map(p=>p.change),[-12.5,null,null,2]);
  assert.equal(result.rows[0].previous_date,'2023-12-29');
  assert.equal(result.rows[0].value,0);
  assert.equal(result.axis.length,366);
  assert.equal(result.lines[0].points[2].x,'02-29');
  assert.equal(inventoryHistory(points,'',0).lines.length,3);
  assert.throws(()=>inventoryHistory([points[0],points[0]]),/重复日期/);
  assert.deepEqual(inventoryHistory([], '',5).rows,[]);
});

test('seasonal defaults follow newest five available years, fewer years all visible', () => {
  const make=years=>years.map(year=>({year,label:`${year}年`}));
  for (let n=0;n<=7;n++) {
    const lines=make(Array.from({length:n},(_,i)=>2022+i));
    assert.equal(Object.values(seasonalSelection(lines)).filter(Boolean).length,Math.min(n,5));
    assert.equal(Object.values(seasonalSelection(lines,true)).filter(Boolean).length,n);
  }
  const lines=make([2026,2020,2025,2018,2023,2024]);
  assert.equal(seasonalSelection(lines)['2018年'],false);
  assert.equal(seasonalSelection(lines)['2020年'],true);
});
test('calendar-only weekend padding connects without inventing data', () => {
  const lines = [{label:'2026',points:[{x:'09-04',value:1},{x:'09-07',value:2}]}];
  const s = lineSegments(['09-04','09-05','09-06','09-07'],lines);
  assert.equal(s.length,1);
  assert.deepEqual(s[0].data,[1,null,null,2]);
});
test('explicit missing trading observation splits the series, zero is valid', () => {
  const s=lineSegments(['a','b','c','d'],[{label:'RB',points:[{x:'a',value:0},{x:'b',value:null},{x:'c',value:2},{x:'d',value:3}]}]);
  assert.equal(s.length,2);
  assert.deepEqual(s.map(x=>x.data),[[0,null,null,null],[null,null,2,3]]);
  assert.ok(s.every(x=>x.name==='RB' && x.lineIndex===0));
});
test('years never join, NaN breaks, off-axis points are ignored', () => {
  const s=lineSegments(['a','b','c'],[{label:'2025',points:[{x:'a',value:1},{x:'b',value:NaN},{x:'c',value:3}]},{label:'2026',points:[{x:'a',value:4},{x:'z',value:5}]}]);
  assert.deepEqual(s.map(x=>x.name),['2025','2025','2026']);
});

test('tooltip resolves split series by original line and data index, not reported name', () => {
  const lines=[{label:'RB2510',points:[{x:'a',value:1,trade_date:'2025-09-01'},{x:'b',value:null},{x:'c',value:3,trade_date:'2025-09-03'}]},
    {label:'RB2610',points:[{x:'c',value:0,trade_date:'2026-09-03'}]}];
  const axis=['a','b','c'], segments=lineSegments(axis,lines), maps=lines.map(l=>new Map(l.points.map(p=>[p.x,p])));
  const rows=tooltipRows([0,1,2,2].map(seriesIndex=>({seriesIndex,dataIndex:2,name:'incorrect name',axisValue:'c',value:'-'})),axis,segments,maps);
  assert.deepEqual(rows.map(r=>[r.name,r.point.trade_date,r.point.value]),[['RB2510','2025-09-03',3],['RB2610','2026-09-03',0]]);
  assert.deepEqual(tooltipRows({seriesIndex:0,dataIndex:1,axisValue:'b'},axis,segments,maps),[]);
  assert.deepEqual(tooltipRows({seriesIndex:1,dataIndex:2,axisValue:'b'},axis,segments,maps),[]);
});


test('secondary highlight stays after the OI leader, with stable ties and no fabricated fallback',()=>{
  const rows=[['IH2611',20666],['IH2612',75788],['IH2703',20035],['IH2706',3712]].map(([symbol,position])=>({symbol,position}));
  assert.deepEqual(holdingLeaders(rows),{activeContract:'IH2612',secondaryContract:'IH2703'});
  assert.deepEqual(holdingLeaders(rows.slice(0,2)),{activeContract:'IH2612',secondaryContract:''});
  assert.deepEqual(holdingLeaders([{symbol:'IH2703',position:10},{symbol:'IH2612',position:10}]),{activeContract:'IH2612',secondaryContract:'IH2703'});
  assert.deepEqual(holdingLeaders([{symbol:'IH2612',position:null},{symbol:'IH2703',position:0}]),{activeContract:'',secondaryContract:''});
  assert.deepEqual(rows.map(r=>r.symbol),['IH2611','IH2612','IH2703','IH2706']);
});
