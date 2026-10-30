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
const module={exports:{}},chartDraws=[];
new Function('module','exports','require',code)(module,module.exports,id=>id.startsWith('react')?sharedRequire(id):id.endsWith('/charts.js')?{...createRequire(import.meta.url)(id),useChart:(_ref,draw)=>chartDraws.push(draw)}:createRequire(new URL('../src/MarketPanels.jsx',import.meta.url))(id));
const {ScanPanel,CurvePanel,Trend,passesScan,RadarPanel,radarRows,BasisPanel,spreadMatrix,SpreadMatrix,SeasonalSpreadChart,RolloverProgress}=module.exports;

test('spread matrix uses recorded last trading days, preserves missing values and marks valid extrema',()=>{
 const trade_date='2026-10-29',row=(symbol,close)=>({symbol,close,trade_date});
 const contracts=[row('IM2706',5986),row('IM2611',6354),row('IM2703',6151.2),row('IM2612',6304.4),row('IM9999',999),{...row('IM2610',6000),trade_date:'2026-10-16'}];
 const terms={as_of:trade_date,contracts:['IM2611','IM2612','IM2703','IM2706'].map((symbol,i)=>({symbol,status:'rds_recorded',last_trade_date:['2026-11-20','2026-12-18','2027-03-19','2027-06-18'][i],last_delivery_date:'2028-01-01'}))};
 const matrix=spreadMatrix(contracts,trade_date,terms);
 assert.deepEqual(matrix.map(r=>r.symbol),['IM2611','IM2612','IM2703','IM2706']);
 assert.deepEqual(matrix.map((r,i)=>r.cells[i].value),[6354,6304.4,6151.2,5986]);
 assert.ok(Math.abs(matrix[0].cells[1].value-49.6)<1e-10);
 assert.equal(matrix[1].cells[0].days,28);
 assert.ok(Math.abs(matrix[1].cells[0].value-49.6/28)<1e-10);
 assert.equal(matrix[3].cells[0].days,210);
 assert.equal(matrix[3].cells[2].days,91);
 assert.equal(matrix[3].cells[0].near,'IM2611');
 assert.equal(matrix[3].cells[0].far,'IM2706');
 assert.equal(matrix[3].cells[2].maximum,true);
 assert.equal(matrix[2].cells[1].minimum,true);
 assert.equal(matrix[0].cells[1].minimum,undefined);
 const html=renderToStaticMarkup(React.createElement(SpreadMatrix,{data:{contracts,contract_terms:terms,meta:{as_of:trade_date},near:'IM2611',far:'IM2612'},onPair:()=>{}}));
 assert.doesNotMatch(html,/最后交易日间隔/);
 assert.doesNotMatch(html,/月距/);
 assert.equal((html.match(/aria-pressed="true"/g)||[]).length,2);
 assert.match(html,/1\.77/);
 assert.equal((html.match(/class="matrix-extreme"/g)||[]).length,2);
 assert.match(html,/title="IM2703 − IM2706；多头移仓参考"/);
 const invalid=[undefined,{...terms,as_of:'2026-10-28'},...['calendar_conflict','calendar_uncovered'].map(status=>({...terms,contracts:terms.contracts.map(c=>({...c,status}))})),...['2026-11-20',null,'invalid'].map(last_trade_date=>({...terms,contracts:terms.contracts.map(c=>({...c,last_trade_date}))}))];
 for(const t of invalid){
  const rows=spreadMatrix(contracts,trade_date,t);
  assert.equal(rows[1].cells[0].value,null);
  assert.equal(rows[1].cells[0].days,null);
  assert.ok(rows.flatMap(r=>r.cells).every(c=>!c.minimum&&!c.maximum));
  assert.ok(Math.abs(rows[0].cells[1].value-49.6)<1e-10);
 }
 const prices=[row('IM2611',0),row('IM2612',null),row('IM2703',10)];
 const missing=spreadMatrix(prices,trade_date,terms);
 assert.equal(missing[0].cells[0].value,0);
 assert.equal(missing[1].cells[0].value,null);
 assert.equal(missing[2].cells[0].value,-10/119);
 assert.equal(missing[2].cells[0].maximum,true);
 assert.equal(missing[2].cells[0].minimum,true);
 const tied=spreadMatrix(prices.map(c=>({...c,close:100})),trade_date,terms);
 assert.equal(tied.flatMap(r=>r.cells).filter(c=>c.maximum&&c.minimum).length,3);
 assert.deepEqual(spreadMatrix(contracts,'2020-01-01',terms),[]);
});

test('seasonal comparison keeps individual observations, zero and current separate from the historical median',()=>{
 const summary={samples:[{year:2024,label:'SH2401 − SH2403',trade_date:'2023-10-27',value:0},{year:2025,label:'SH2501 − SH2503',trade_date:'2024-10-28',value:null},{year:2026,label:'SH2601 − SH2603',trade_date:'2025-10-29',value:-20}],current:{year:2027,label:'SH2701 − SH2703',trade_date:'2026-10-29',value:80},median:-10};
 const html=renderToStaticMarkup(React.createElement(SeasonalSpreadChart,{summary}));
 let option;chartDraws.pop()({setOption:value=>{option=value;}});
 assert.deepEqual(option.yAxis.data,['2027 当前','2026','2024']);
 assert.deepEqual(option.series[0].data.map(p=>p.value),[[80,0],[-20,1],[0,2]]);
 assert.equal(option.series[0].markLine.data[0].xAxis,-10);
 assert.equal(option.series[0].lineStyle.opacity,0);
 assert.equal(option.yAxis.axisLabel.interval,0);
 assert.equal(option.series[0].showAllSymbol,true);
 assert.equal(option.series[0].data[0].itemStyle.color,'#4e7fb9');
 assert.equal(option.tooltip.formatter({dataIndex:1}),'SH2601 − SH2603\n2025-10-29：-20');
 assert.match(html,/>90<\/strong>/);
 assert.equal(option.series[0].data[0].symbol,'diamond');
 const tenYears=renderToStaticMarkup(React.createElement(SeasonalSpreadChart,{summary:{...summary,samples:Array.from({length:9},(_,i)=>({...summary.samples[0],year:2018+i,value:i}))}}));
 assert.match(tenYears,/class="seasonal-spread-chart"/);
 chartDraws.pop();
 assert.doesNotMatch(html,/<table/);
 const empty=renderToStaticMarkup(React.createElement(SeasonalSpreadChart,{summary:{samples:[],current:null,median:null}}));
 assert.match(empty,/暂无同期历史样本/);assert.doesNotMatch(empty,/role="img"/);
});

test('seasonal spread follows selected legs and date despite a saved month preference',async()=>{
 const source=await readFile(new URL('../src/HistoryPanel.jsx',import.meta.url),'utf8');
 const code=ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 const queries=[],module={exports:{}};
 new Function('module','exports','require',code)(module,module.exports,id=>{
  if(id.endsWith('/charts.js'))return {usePreference:(key,initial)=>[key.endsWith('.month')?1:initial,()=>{}],useResource:url=>{queries.push(new URL(url,'http://test').searchParams);return {data:null,busy:true};}};
  return id.startsWith('react')?sharedRequire(id):createRequire(new URL('../src/HistoryPanel.jsx',import.meta.url))(id);
 });
 const props={product:'RB',end:'2026-10-28',near:'RB2701',far:'RB2703',priceType:'close',view:'spread'};
 for(const change of [{},{near:'RB2612'},{near:'RB2612',far:'RB2705'},{near:'RB2611',far:'RB2612',end:'2026-10-14'},{view:'month',near:'RB2612'},{product:'SC',near:'SC2611',far:'SC2909'},{product:'SC',near:'SC2903',far:'SC2909'}]){
  renderToStaticMarkup(React.createElement(module.exports.default,{...props,...change}));
 }
 assert.deepEqual(queries.map(q=>['near_month','far_month','far_year_offset','anchor_year','end'].map(k=>q.get(k))),[
  ['1','3','0','2027','2026-10-28'],['12','3','1','2026','2026-10-28'],['12','5','1','2026','2026-10-28'],
  ['11','12','0','2026','2026-10-14'],['1','3','1','2026','2026-10-28'],
  ['11','9','3','2026','2026-10-28'],['3','9','0','2029','2026-10-28'],
 ]);
});
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
 assert.match(html,/type="submit"[^>]*>.*更新中/);
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


test('rollover overlay uses separate relative-day axes and real dates without joining missing observations',()=>{
 const data={rounds:[{name:'current',outgoing:'IM2612',incoming:'IM2703',series:[{day:-365,trade_date:'2025-12-19',outgoing:null,incoming:null,spread:null},{day:-50,trade_date:'2026-10-29',outgoing:100,incoming:50,spread:15}]},{name:'previous',outgoing:'IM2609',incoming:'IM2612',series:[{day:0,trade_date:'2026-09-18',outgoing:0,incoming:70,spread:null}]}]};
 const html=renderToStaticMarkup(React.createElement(RolloverProgress,{data}));
 let option;chartDraws.pop()({setOption:value=>{option=value;}});
 assert.equal(option.series.length,5);
 assert.equal(option.series.at(-1).yAxisIndex,1);
 assert.deepEqual(option.series.map(s=>s.name),['IM2612','IM2703','IM2609 · 上轮','IM2612 · 上轮','价差']);
 assert.deepEqual(option.series.map(s=>s.itemStyle.color),['#4e7fb9','#568f84','#4e7fb9','#568f84','#222222']);
 assert.doesNotMatch(html,/对齐口径|候选/);
 assert.equal(option.series[3].lineStyle.type,'dashed');
 assert.deepEqual(option.series[2].data[0].value,[0,0]);
 assert.equal(option.series[0].data[0].trade_date,'2026-10-29');
 assert.ok(option.series.every(s=>s.connectNulls===false));
 assert.equal(option.xAxis.max,0);
 assert.equal(option.xAxis.min,'dataMin');
 assert.equal(option.dataZoom[0].startValue,-50);
 const hover=option.tooltip.formatter(option.series.map(s=>({seriesName:s.name,data:s.data[0],value:s.data[0].value})));
 assert.equal((hover.match(/2026-10-29/g)||[]).length,1);
 assert.equal((hover.match(/2026-09-18/g)||[]).length,1);
 assert.equal(hover.split('\n').at(-1),'价差：15');
 assert.match(html,/自然日对齐/);
});


async function sharedHooks(overrides={}) {
 const source=await readFile(new URL('../../../frontend/src/charts.js',import.meta.url),'utf8');
 const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 const slots=[],effects=[],module={exports:{}};let index=0;
 const hooks={...React,useState:initial=>{const i=index++;slots[i]||={value:typeof initial==='function'?initial():initial};return [slots[i].value,next=>{slots[i].value=typeof next==='function'?next(slots[i].value):next;}];},
  useRef:value=>slots[index++]||=( {current:value} ),
  useEffect:(effect,deps)=>{const i=index++,old=slots[i];if(!old||!deps||deps.some((v,j)=>!Object.is(v,old.deps[j])))effects.push(()=>{old?.cleanup?.();slots[i]={deps,cleanup:effect()};});}};
 new Function('module','exports','require',code)(module,module.exports,id=>id==='react'?hooks:overrides[id]||{});
 return {render:(name,...args)=>{index=0;const value=module.exports[name](...args);effects.splice(0).forEach(fn=>fn());return value;},unmount:()=>slots.forEach(slot=>slot.cleanup?.())};
}
const settleHooks=()=>new Promise(resolve=>setImmediate(resolve));

test('selection requests retain the previous view, cancel stale responses and distinguish errors',async()=>{
 const requests=[];
 const h=await sharedHooks({'./transport.js':{conditionalFetch:(url,{signal})=>new Promise((resolve,reject)=>requests.push({url,signal,resolve:data=>resolve({ok:true,json:async()=>data}),reject}))}});
 assert.equal(h.render('useResource','/a').busy,true);
 requests[0].resolve({product:'A'});await settleHooks();
 assert.equal(h.render('useResource','/a').data.product,'A');
 const pending=h.render('useResource','/b');assert.equal(pending.busy,true);assert.equal(pending.data.product,'A');
 h.render('useResource','/c');assert.equal(requests[1].signal.aborted,true);
 requests[1].resolve({product:'B'});requests[2].resolve({product:'C'});await settleHooks();
 assert.deepEqual(h.render('useResource','/c'),{data:{product:'C'},error:undefined,busy:false});
 assert.equal(h.render('useResource','/d',0,'other').data,null);
 requests[3].reject(Error('offline'));await settleHooks();
 assert.deepEqual(h.render('useResource','/d',0,'other'),{data:null,error:'offline',busy:false});
 h.unmount();assert.equal(requests[3].signal.aborted,true);
});

test('chart updates reuse the instance and dispose only when the DOM node leaves',async()=>{
 const oldIntersection=globalThis.IntersectionObserver,oldResize=globalThis.ResizeObserver;
 let created=0,disposed=0,draws=0;
 globalThis.IntersectionObserver=class{constructor(callback){this.callback=callback;}observe(){queueMicrotask(()=>this.callback([{isIntersecting:true}]));}disconnect(){}};
 globalThis.ResizeObserver=class{observe(){}disconnect(){}};
 try{
  const h=await sharedHooks({'./chart-engine.js':{init:()=>{created++;return {dispose:()=>disposed++,resize(){}};}}});
  const ref={current:{}},draw=()=>draws++;
  h.render('useChart',ref,draw,[1]);await settleHooks();
  h.render('useChart',ref,draw,[2]);await settleHooks();
  assert.equal(created,1);assert.equal(disposed,0);assert.equal(draws,2);
  h.render('useChart',ref,draw,[2]);assert.equal(draws,2);
  ref.current=null;h.render('useChart',ref,draw,[2]);assert.equal(disposed,1);
  ref.current={};h.render('useChart',ref,draw,[3]);await settleHooks();
  assert.equal(created,2);h.unmount();assert.equal(disposed,2);
 }finally{globalThis.IntersectionObserver=oldIntersection;globalThis.ResizeObserver=oldResize;}
});
