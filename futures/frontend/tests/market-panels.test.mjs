import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {linkZoom} from '../../../frontend/src/charts.js';
const sharedRequire=createRequire(new URL('../../../frontend/package.json',import.meta.url));
const React=sharedRequire('react'), {renderToStaticMarkup}=sharedRequire('react-dom/server');

const source=await readFile(new URL('../src/MarketPanels.jsx',import.meta.url),'utf8');
const ts=sharedRequire('typescript');
const code=ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
const module={exports:{}};
new Function('module','exports','require',code)(module,module.exports,id=>id.startsWith('react')?sharedRequire(id):createRequire(import.meta.url)(id));
const {ScanPanel,CurvePanel,Trend,passesScan,RadarPanel,radarRows,BasisPanel}=module.exports;
test('linked date charts restore late mounts, synchronize both ways and detach without echoes',()=>{
 const chart=()=>({range:{start:0,end:100},calls:0,handlers:new Map(),
  on(name,fn){this.handlers.set(name,fn);},off(name){this.handlers.delete(name);},
  dispatchAction({start,end},options){assert.equal(options.silent,true);this.range={start,end};this.calls++;},
  drag(start,end,batch=false){this.range={start,end};this.handlers.get('datazoom')?.(batch?{batch:[this.range]}:this.range);}});
 const group=new Set(),a=chart(),b=chart(),outside=chart();
 const detach=linkZoom(a,group);linkZoom(outside,new Set());
 a.drag(20,70);const detachB=linkZoom(b,group);
 assert.deepEqual(b.range,{start:20,end:70});
 b.drag(40,90,true);assert.deepEqual(a.range,b.range);assert.equal(b.calls,1);
 assert.deepEqual(outside.range,{start:0,end:100});
 detach();b.drag(0,100);assert.deepEqual(a.range,{start:40,end:90});
 linkZoom(a,group);assert.deepEqual(a.range,{start:0,end:100});
 detachB();assert.equal(b.handlers.size,0);assert.equal(group.has(b),false);
});
test('linked contract legends synchronize selections, late mounts and cleanup without affecting ratios',()=>{
 const chart=()=>({selected:{},handlers:new Map(),calls:0,
  on(name,fn){this.handlers.set(name,fn);},off(name){this.handlers.delete(name);},
  dispatchAction({type,name},options){assert.equal(options.silent,true);this.selected[name]=type==='legendSelect';this.calls++;},
  select(name,value){this.selected[name]=value;this.handlers.get('legendselectchanged')?.({name,selected:{...this.selected}});}});
 const group=new Set(),holdings=chart(),changes=chart(),ratio=chart(),prices=chart();
 linkZoom(holdings,group,true);const detach=linkZoom(changes,group,true);linkZoom(ratio,group);
 holdings.select('RB2701',false);assert.equal(changes.selected.RB2701,false);assert.equal(ratio.calls,0);
 linkZoom(prices,group,true);assert.equal(prices.selected.RB2701,false);
 prices.select('RB2701',true);assert.equal(holdings.selected.RB2701,true);assert.equal(changes.selected.RB2701,true);
 changes.select('RB2705',false);assert.equal(prices.selected.RB2705,false);assert.equal(holdings.selected.RB2705,false);
 detach();holdings.select('RB2705',true);assert.equal(changes.selected.RB2705,false);assert.equal(changes.handlers.size,0);assert.equal(group.legendCharts.has(changes),false);
 assert.equal(ratio.calls,0);
});

test('radar displays authoritative objects without reclassifying product summary',()=>{
 const objects=[{key:'IM:IM2610/IM2612',product_summary_level:'普通观察',bucket:'优先核查',direction:'卖 IM2610 / 买 IM2612',events:[]}];
 assert.equal(radarRows({objects}),objects);
 assert.deepEqual(radarRows(null),[]);
 assert.doesNotMatch(source,/const (priority|directional|deliveryMonth)=/);
 assert.match(source,/new URLSearchParams\(\{\.\.\.query,family,price_type:'close'\}\)/);
 assert.match(source,/\[filter,setFilter\]=usePreference\('futures.radar.filter','priority'\)/);
 assert.match(source,/当前筛选无结果/);
});
test('radar keeps small result lists visible and carries candidate evidence into pair research',()=>{
 assert.match(source,/items.length<=10\?' short-results'/);
 assert.match(source,/direction_code:r.direction_code/);
 assert.match(source,/e.far\?'跨期分析'/);
});
test('basis keeps one control row, meaningful units and compact formula hints',()=>{
 const html=renderToStaticMarkup(React.createElement(BasisPanel,{product:'CU',priceType:'close',controls:()=>React.createElement('span',null,'controls')}));
 assert.match(html,/controls/);assert.match(html,/读取基差/);
 assert.match(source,/isIndex\?'点':'元\/吨'/);
 assert.match(source,/正值贴水，负值升水/);
 assert.match(source,/非年化/);
 assert.match(source,/测试样本/);
 const basis=source.slice(source.indexOf('export function BasisPanel'),source.indexOf('export function SpreadDirection'));
 assert.doesNotMatch(basis,/<details|ContractTerms|<table/);
});

test('radar starts with all-product controls and does not require choosing a product',()=>{
 const html=renderToStaticMarkup(React.createElement(RadarPanel,{onSelect(){}}));
 assert.match(html,/扫描全部品种/);
 assert.match(html,/name="min_volume"/);
 assert.match(html,/name="min_position"/);
 assert.doesNotMatch(html,/name="product"/);
 assert.match(source,/e.family==='spread'/);
});
test('scan thresholds preserve default universe and reject missing liquidity when requested',()=>{
 assert.equal(passesScan({}),true);
 assert.equal(passesScan({},0,1),false);
 const o={latest:{history_n:37,near_volume:60078,far_volume:16483}};
 assert.equal(passesScan(o,37,16483),true);
 assert.equal(passesScan(o,38,0),false);
 assert.equal(passesScan(o,0,16484),false);
});
const meta={as_of:'2026-09-07'};
test('holdings drilldown uses the actual API share field',async()=>{
 const app=await readFile(new URL('../src/App.jsx',import.meta.url),'utf8');
 assert.match(app,/"new_oi_share"/);
 assert.doesNotMatch(app,/"new_position_share"/);
 assert.doesNotMatch(app,/start:result.start|start=\{q.start\}|dateWindow/);
});

test('mini trend preserves zero, splits explicit gaps, and handles single observations',()=>{
 const html=renderToStaticMarkup(React.createElement(Trend,{points:[0,1,null,2,3].map((spread,i)=>({trade_date:`2026-09-0${i+1}`,spread}))}));
 assert.equal((html.match(/<polyline/g)||[]).length,2);
 assert.match(html,/4个有效配对/);
 assert.match(renderToStaticMarkup(React.createElement(Trend,{points:[{trade_date:'2026-09-01',spread:0}]})),/<circle/);
 assert.match(renderToStaticMarkup(React.createElement(Trend)),/无走势样本/);
});
test('scan keeps zero liquidity, missing leg null, sample and stale-pair warnings visible',()=>{
 const html=renderToStaticMarkup(React.createElement(ScanPanel,{onPair(){},data:{meta,opportunities:[{near:'IM2609',far:'IM2612',sample_count:21,missing_dates:[],latest:{trade_date:'2026-09-04',spread:0,change:0,history_n:20,historical_percentile:0,near_volume:0,far_volume:200,near_position:null,far_position:200}}]}}));
 assert.match(html,/配对日期落后/);assert.match(html,/量仓缺失/);assert.match(html,/0%/);assert.match(html,/<td class="num">0<\/td>/);assert.match(html,/<td class="num">—<\/td>/);assert.match(html,/<th class="num">价差<\/th>/);assert.match(html,/<th>组合 \/ 配对日期<\/th>/);assert.doesNotMatch(html,/跨期价差轨迹/);
});
test('curve uses supplied exact-date columns and preserves null historical values',()=>{
 const charts=[];const Chart=props=>{charts.push(props);return null;};
 const html=renderToStaticMarkup(React.createElement(CurvePanel,{onPair(){},Chart,data:{meta,curve:{nodes:[{symbol:'IM2609',price:100,volume:0,position:20}],adjacent:[],comparisons:[{lag:1,as_of:'2026-09-04',nodes:[{symbol:'IM2609',price:null,change:null}]},{lag:5,as_of:null,nodes:[{symbol:'IM2609',price:null,change:null}]}]}}}));
 assert.equal(charts[0].rows[0].lag1,null);assert.equal(charts[0].lines.length,2);assert.equal(charts.length,1);assert.match(charts[0].tooltip([{dataIndex:0,marker:'',seriesName:'前1个观测日',value:null}]),/前1个观测日：—.*成交 0 手 · 持仓 20 手/);assert.doesNotMatch(html,/选取近月|选取远月|分析所选两腿|成交持仓与合约明细/);assert.match(html,/窗口内观测不足/);assert.doesNotMatch(html,/历史分位/);
});

test('curve keeps the shared selection and drills adjacent pairs into history',()=>{
 const charts=[];let selected;
 const data={meta,near:'IM2609',far:'IM2612',curve:{nodes:[{symbol:'IM2609',price:100,volume:0,position:null},{symbol:'IM2612',price:90,volume:8,position:10}],comparisons:[],adjacent:[{near:'IM2609',far:'IM2612',spread:10}]}};
 const element=CurvePanel({data,onPair:(...pair)=>{selected=pair;},Chart:props=>{charts.push(props);return null;},controls:React.createElement('span',null,'共享筛选')});
 const html=renderToStaticMarkup(element);
 assert.deepEqual(charts[0].selected,['IM2609','IM2612']);assert.match(html,/共享筛选/);assert.match(html,/查看价差季节性/);
 const adjacent=element.props.children.at(-1);adjacent.props.children[1][0].props.onClick();
 assert.deepEqual(selected,['IM2609','IM2612']);
 const empty=renderToStaticMarkup(React.createElement(CurvePanel,{data:{meta,curve:{nodes:[],comparisons:[],adjacent:[]}},Chart:()=>{throw Error('empty chart rendered');}}));
 assert.match(empty,/当前日期暂无期限曲线数据/);
});
