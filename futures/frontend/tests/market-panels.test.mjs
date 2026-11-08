import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {linkZoom,axisTooltip,formatPrice} from '../../../frontend/src/charts.js';
const sharedRequire=createRequire(new URL('../../../frontend/package.json',import.meta.url));
const React=sharedRequire('react'), {renderToStaticMarkup}=sharedRequire('react-dom/server');

const source=await readFile(new URL('../src/MarketPanels.jsx',import.meta.url),'utf8');
const ts=sharedRequire('typescript');
const matrixModule={exports:{}};
new Function('module','exports','require',ts.transpileModule(await readFile(new URL('../../../frontend/src/SpreadMatrix.jsx',import.meta.url),'utf8'),{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText)(matrixModule,matrixModule.exports,id=>id.endsWith('.css')?{}:id==='./charts.js'?sharedRequire('./src/charts.js'):sharedRequire(id));
const code=ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
const module={exports:{}},chartDraws=[];
new Function('module','exports','require',code)(module,module.exports,id=>id.endsWith('/SpreadMatrix.jsx')?matrixModule.exports:id.startsWith('react')?sharedRequire(id):id.endsWith('/charts.js')?{...createRequire(import.meta.url)(id),useChart:(_ref,draw)=>chartDraws.push(draw)}:createRequire(new URL('../src/MarketPanels.jsx',import.meta.url))(id));
const {ScanPanel,CurvePanel,Trend,passesScan,RadarPanel,radarRows,BasisPanel,BasisPrices,BasisDistribution,basisHistory,basisDistribution,spreadMatrix,SpreadMatrix,SeasonalSpreadChart,RolloverProgress}=module.exports;

test('option formats preserve rounding and smile/spot render in one update',async()=>{
 const source=await readFile(new URL('../../../frontend/src/Options.jsx',import.meta.url),'utf8'),draws=[],module={exports:{}};
 const code=ts.transpileModule(source+'\nexport {formatNumber,formatPrice,VolatilityTab};',{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 new Function('module','exports','require',code)(module,module.exports,id=>id.endsWith('.css')?{}:id==='./charts.js'?{...sharedRequire('./src/charts.js'),useChart:(_ref,draw)=>draws.push(draw),usePreference:()=>['single',()=>{}]}:id==='./SurfaceComparison.jsx'?{IvHistory:()=>null}:sharedRequire(id.startsWith('./')?'./src/'+id.slice(2):id));
 for(const digits of [0,1,2,3])for(const value of [null,undefined,'',NaN,Infinity,0,-1234.5678,'1234.5678']){
  const n=value===null||value===undefined||value===''?NaN:Number(value);
  assert.equal(module.exports.formatNumber(value,digits),Number.isFinite(n)?n.toLocaleString('en-US',{minimumFractionDigits:digits,maximumFractionDigits:digits}):'—');
  assert.equal(module.exports.formatPrice(value,digits),Number.isFinite(n)?n.toLocaleString('zh-CN',{useGrouping:false,minimumFractionDigits:digits,maximumFractionDigits:digits}):'—');
  assert.equal(module.exports.formatNumber(value,digits,false),module.exports.formatPrice(value,digits));
 }
 const rows=['C','P'].map(cp=>({month:'202612',cp,strike:6000,iv:.3,code:'MO2612-'+cp+'-6000'})),month={id:'202612'};
 renderToStaticMarkup(React.createElement(module.exports.VolatilityTab,{rows,month,months:[month],spot:6010,snapshot:{}}));
 const options=[];for(const draw of draws)draw({setOption:option=>options.push(option)});
 assert.equal(options.length,1);assert.deepEqual(options[0].series[0].markLine.data,[{xAxis:6010}]);
 assert.deepEqual(options[0].series.map(s=>s.data),[[[6000,30]],[[6000,30]]]);
});

test('intraday chart keeps the trading session, real gaps and independent price/IV scales',async()=>{
 const draws=[],module={exports:{}};
 const code=ts.transpileModule(await readFile(new URL('../../../frontend/src/IntradayOptions.jsx',import.meta.url),'utf8'),{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 new Function('module','exports','require',code)(module,module.exports,id=>id==='./charts.js'?{axisTooltip,formatPrice,ChartFrame:({children})=>children,useChart:(_ref,draw)=>draws.push(draw)}:id==='./intraday-live.js'?sharedRequire('./src/intraday-live.js'):sharedRequire(id));
 const data={series:'MO2611',trade_date:'2026-11-03',future_code:'IM2611.CFE',points:[{time:'09:31',future_last:6300,atm_iv:.30,strike:6300},{time:'09:33',future_last:6301,atm_iv:null}]};
 const draw=intraday=>{
  draws.length=0;
  renderToStaticMarkup(React.createElement(module.exports.default,{catalog:{codes:[],metadata:{}},quotes:new Map([['IM2611.CFE',{prev_close:6320}]]),series:'MO2611',intraday}));
  const options=[];for(const draw of draws)draw({setOption:option=>options.push(option)});
  return options.find(option=>option.xAxis[0]?.type==='category');
 };
 const chart=draw(data);
 assert.equal(chart.xAxis[0].data.length,242);
 assert.equal(chart.xAxis[0].data[0],'09:30');assert.equal(chart.xAxis[0].data.at(-1),'15:00');
 assert.equal(chart.xAxis[0].data.some(time=>time.startsWith('12:')),false);
 assert.equal(chart.tooltip.axisPointer.type,'cross');
 assert.equal(chart.dataZoom.some(zoom=>zoom.type==='slider'),false);
 assert.deepEqual(chart.series[0].data.slice(0,4).map(p=>p[1]),[null,6300,null,6301]);
 assert.equal(chart.series[0].data.at(-1)[1],null);
 assert.deepEqual(chart.series[0].markPoint.data[0].coord,['09:33',6301]);
 assert.equal(chart.series[1].data[1][1],30);assert.equal(chart.series[1].data[3][1],null);
 assert.equal(chart.legend,undefined);assert.equal(chart.yAxis[0].position,'right');assert.equal(chart.yAxis[1].show,false);
 assert.equal(chart.yAxis[0].axisLabel.formatter(12345.67),'12345.67');
 assert.equal(chart.yAxis[0].axisPointer.label.formatter({value:12345.67}),'12345.67');
 assert.deepEqual(chart.visualMap[0].pieces.map((piece,i)=>i?piece.min:piece.max),[6320,6320]);
 assert.deepEqual(chart.series.map(series=>series.yAxisIndex),[0,1]);
 assert.equal(chart.series[1].itemStyle.color,'#3d70ad');
 assert.equal(chart.series.every(series=>series.connectNulls===false),true);
 const lunch=draw({...data,volume_unit:'contracts',points:[...data.points,{time:'11:29',future_last:6290,future_volume:60},{time:'11:30',future_last:null,future_volume:null},{time:'13:00',future_last:6240,future_volume:0},{time:'13:01',future_last:null},{time:'13:03',future_last:6241}]});
 const bridge=lunch.series[0].markLine.data.find(Array.isArray);
 assert.deepEqual(bridge.map(p=>p.coord),[['11:29',6290],['13:00',6240]]);
 const quantity=lunch.series.find(series=>series.id==='future_volume');
 assert.equal(quantity.type,'bar');assert.equal(quantity.yAxisIndex,2);assert.equal(lunch.yAxis[2].show,false);
 assert.equal(quantity.data.find(p=>p.value[0]==='13:00').value[1],0);assert.equal(quantity.data.find(p=>p.value[0]==='11:30').value[1],null);
 assert.equal(draw({...data,points:[{time:'09:31',future_last:6300,future_volume:123}]}).series.some(series=>series.id==='future_volume'),false);
 assert.equal(bridge[0].lineStyle.type,'dashed');assert.equal(bridge[0].label.show,false);
 assert.equal(lunch.series[0].data.find(p=>p[0]==='11:30')[1],null);
 assert.equal(lunch.series[0].data.find(p=>p[0]==='13:01')[1],null);
 assert.equal(draw({...data,points:[{time:'11:28',future_last:6290},{time:'13:00',future_last:6240}]}).series[0].markLine.data.some(Array.isArray),false);
 const ivOnly=draw({...data,future_code:null});
 assert.equal(ivOnly.yAxis[0].show,false);assert.equal(ivOnly.yAxis[1].show,true);
 assert.deepEqual(ivOnly.series.map(series=>series.id),['atm_iv']);
 const renderer=sharedRequire('echarts').init(null,null,{renderer:'svg',ssr:true,width:600,height:320});
 try{
  renderer.setOption(lunch);assert.match(renderer.renderToSVGString(),/<svg/);
  const main=renderer.getModel().getComponent('grid',0).coordinateSystem.getRect(),volume=renderer.getModel().getComponent('grid',1).coordinateSystem.getRect();
  assert.ok(volume.y>main.y+main.height+10,'volume has its own pane below both curves');
  for(const seriesIndex of [0,1,2])for(const point of lunch.series[seriesIndex].data){
   const coord=point.value||point;if(!Number.isFinite(coord[1]))continue;
   const y=renderer.convertToPixel({seriesIndex},coord)[1];
   assert.ok(seriesIndex===2?y>=volume.y:y<=main.y+main.height+1);
  }
  assert.deepEqual(lunch.dataZoom[0].xAxisIndex,[0,1]);assert.equal(lunch.series[2].xAxisIndex,1);
  renderer.setOption(ivOnly,{replaceMerge:['series','visualMap','grid','xAxis','yAxis']});assert.match(renderer.renderToSVGString(),/<svg/);
  assert.equal(renderer.getModel().getComponent('grid',1),undefined);assert.equal(renderer.getModel().getComponent('yAxis',2),undefined);
 }finally{renderer.dispose();}
});

test('intraday cold start and source-selected commodity codes keep candidate metadata off the page',async t=>{
 const originalLocation=globalThis.location;t.after(()=>{globalThis.location=originalLocation;});globalThis.location={search:'?series=MO2611'};
 const source=(await readFile(new URL('../../../frontend/src/Intraday.jsx',import.meta.url),'utf8')).replace(/import\.meta\.env\.VITE_INTRADAY_\w+/g,'undefined');
 const code=ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 const selected=['CU2612.SHF','CU2701.SHF','CU2702.SHF'],all=[...selected,'CU2712.SHF'];
 const feed={connection:'connected',health:{source_mode:'wind'},catalog:{codes:all,commodity_selection:{CU:selected},metadata:Object.fromEntries(all.map((code,i)=>[code,{kind:'future',product:'CU',product_name:'铜',unit:'CNY/t',last_trade_date:['2026-12-15','2027-01-15','2027-02-15','2027-12-15'][i]}]))},quotes:new Map(all.map((code,i)=>[code,{last:60000+i,oi:i===3?999999:1,acquired_at:'2026-11-03T15:00:00+08:00'}]))};
 function render(data){
  const module={exports:{}};
  new Function('module','exports','require',code)(module,module.exports,id=>id.endsWith('.css')?{}:id==='react'?{...React,lazy:()=>()=>null,useState:initial=>{const value=typeof initial==='function'?initial():initial;return React.useState(data&&value?.quotes instanceof Map?data:value);}}:id==='./preferences.js'?{usePreference:(key,initial)=>[key==='intraday.series'?'MO2611':initial,()=>{}]}:id==='./SpreadMatrix.jsx'?matrixModule.exports:id==='./IntradayOptions.jsx'?{default:()=>null}:sharedRequire(id.startsWith('./')?'./src/'+id.slice(2):id));
  return renderToStaticMarkup(React.createElement(module.exports.default));
 }
 assert.match(render(),/月份待接入/);
 const html=render(feed);assert.match(html,/CU\/铜/);assert.match(html,/intraday-commodities/);assert.doesNotMatch(html,/CU2712/);assert.equal((html.match(/<td /g)||[]).length,9);
 feed.catalog.commodity_selection.CU=[];assert.doesNotMatch(render(feed),/CU\/铜/);
 delete feed.catalog.commodity_selection;assert.match(render(feed),/CU2712/);
 feed.catalog.codes=selected;assert.doesNotMatch(render(feed),/CU2712/);
 feed.catalog={codes:[],metadata:{},commodity_selection:{}};feed.quotes=new Map();
 for(const [product,name] of [['P','棕榈油'],['M','豆粕'],['CF','棉花'],['SH','烧碱']]){
  const codes=(['SH','CF'].includes(product)?['701','705','707','709']:['2701','2705','2707','2709']).map(month=>product+month);feed.catalog.commodity_selection[product]=codes.slice(0,3);
  for(const code of codes){feed.catalog.codes.push(code);feed.catalog.metadata[code]={kind:'future',product,product_name:name,unit:'CNY/t',last_trade_date:'2027-'+code.slice(-2)+'-15'};feed.quotes.set(code,{last:3000,oi:code.endsWith('09')?999999:1});}
 }
 const commodities=render(feed),order=['SH/烧碱','M/豆粕','CF/棉花','P/棕榈油'].map(name=>commodities.indexOf(name));
 assert.ok(order.every((position,i)=>position>=0&&(!i||position>order[i-1])));assert.doesNotMatch(commodities,/SH2709|CF2709|P2709|M2709/);assert.equal((commodities.match(/<td /g)||[]).length,36);assert.match(commodities,/SH2701/);assert.match(commodities,/CF2705/);
 const future='IM2611.CFE',spot='000852.SH';
 feed.catalog={codes:[spot,future],metadata:{[spot]:{kind:'index',name:'中证1000'},[future]:{kind:'future',underlying:spot,multiplier:200,last_trade_date:'2026-11-20'}}};
 feed.quotes=new Map([[spot,{last:12345.67,acquired_at:'2026-11-06T15:00:00+08:00'}],[future,{last:12340.6,oi:1234567,acquired_at:'2026-11-06T15:00:00+08:00'}]]);
 const index=render(feed);assert.match(index,/>12345.67</);assert.match(index,/>12340.6</);assert.doesNotMatch(index,/12,345|12,340/);assert.match(index,/0.1 \/ 72.4/);assert.match(index,/1,234,567 \/ 14/);assert.doesNotMatch(index,/>最新价<|>绝对收益<|>到期天数</);
 feed.catalog={codes:[],metadata:{}};feed.quotes=new Map();
 for(const [i,product] of ['IH','IF','IC','IM'].entries()){
  const index='000'+['016','300','905','852'][i]+'.SH';
  feed.catalog.codes.push(index);feed.catalog.metadata[index]={kind:'index',name:product};feed.quotes.set(index,{last:100-i,prev_close:100,acquired_at:'2026-11-06T15:00:00+08:00'});
  for(const month of ['2611','2612','2703','2706']){
   const contract=product+month+'.CFE';feed.catalog.codes.push(contract);feed.catalog.metadata[contract]={kind:'future',underlying:index,multiplier:i<2?300:200,last_trade_date:month.startsWith('26')?'2026-12-18':'2027-06-18'};
   feed.quotes.set(contract,{last:90-i,oi:1234567,acquired_at:'2026-11-06T15:00:00+08:00'});
  }
 }
 const comparison=render(feed);
 assert.equal((comparison.match(/class="intraday-comparison-month"/g)||[]).length,4);
 assert.doesNotMatch(comparison,/IM · 基准|intraday-benchmark/);
 assert.equal((comparison.match(/class="intraday-rate-bar"/g)||[]).length,32);
 assert.doesNotMatch(comparison,/基差对照月份|以IM为基准| pp/);
 for(const [table] of comparison.matchAll(/<table class="intraday-basis">[\s\S]*?<\/table>/g))assert.doesNotMatch(table,/<small>|<br/);
 assert.doesNotMatch(comparison,/>同月基差 · /);assert.match(comparison,/>年化基差</);assert.match(comparison,/>绝对 \/ 日均收益</);assert.match(comparison,/>持仓量 \/ 天数</);
 assert.match(comparison,/left:50%;width:0%/);
 assert.equal((comparison.match(/class="intraday-strength"/g)||[]).length,4);assert.doesNotMatch(comparison,/intraday-strength-values|相对强弱预览/);assert.match(comparison,/涨跌点数 \/ 涨跌幅 \/ 较中证1000/);assert.match(comparison,/<span class="direction-short">-1 \/ -1%<\/span>/);


});

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
 const liveTerms={...terms,contracts:terms.contracts.map(c=>({...c,status:'wind_recorded'}))};
 assert.deepEqual(spreadMatrix(contracts,trade_date,liveTerms),matrix);
 const readonly=renderToStaticMarkup(React.createElement(SpreadMatrix,{data:{contracts,contract_terms:liveTerms,meta:{as_of:trade_date}}}));
 assert.doesNotMatch(readonly,/<button|选择组合|aria-pressed/);
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
test('date zoom links unequal history starts by actual dates and clamps to available data',()=>{
 const dates=['2020-01-01','2020-01-02','2020-01-03','2020-01-06'];
 const chart=axis=>({axis,reads:0,handlers:new Map(),range:{startValue:0,endValue:axis.length-1},
  on(name,fn){this.handlers.set(name,fn);},off(name){this.handlers.delete(name);},
  getOption(){this.reads++;return {xAxis:[{data:this.axis}],dataZoom:[this.range]};},
  dispatchAction({startValue,endValue},options){assert.equal(options.silent,true);this.range={startValue:this.axis.indexOf(startValue),endValue:this.axis.indexOf(endValue)};},
  drag(startValue,endValue){this.range={startValue,endValue};this.handlers.get('datazoom')({start:0,end:100});}});
 const group=Object.assign(new Set(),{byDate:true}),full=chart(dates),paired=chart(dates.slice(1));
 linkZoom(full,group);full.drag(0,2);const detach=linkZoom(paired,group);
 assert.deepEqual(paired.range,{startValue:0,endValue:1});
 assert.equal(paired.reads,1);
 paired.drag(1,2);assert.deepEqual(full.range,{startValue:2,endValue:3});
 assert.equal(full.reads,2);
 full.drag(0,0);assert.deepEqual(paired.range,{startValue:0,endValue:0});
 assert.equal(paired.reads,2);detach();assert.equal(group.dateAxes.has(paired),false);
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
 assert.match(html,/controls/);assert.match(html,/商品基差暂未接入/);
 assert.doesNotMatch(html,/>CU<|读取基差/);
 assert.equal((html.match(/<button/g)||[]).length,4);
 assert.match(renderToStaticMarkup(React.createElement(BasisPanel,{product:'IH',priceType:'close'})),/读取基差/);
 assert.match(source,/走势图/);
 assert.match(source,/现货 − 期货/);
 assert.match(source,/基差 ÷ 现货 × 365 ÷ 距交割日天数/);
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
 assert.equal(charts[0].rows[0].lag1,null);assert.equal(charts[0].lines.length,2);assert.equal(charts.length,1);assert.equal(charts[0].tooltip([{dataIndex:0,seriesName:'前1个观测日',value:null}]),'');const tip=charts[0].tooltip([{dataIndex:0,seriesName:'当日',value:100},{dataIndex:0,seriesName:'前1个观测日',value:null}]);assert.doesNotMatch(tip,/前1个观测日|—/);assert.match(tip,/成交 0 手\n持仓 20 手/);assert.doesNotMatch(html,/选取近月|选取远月|分析所选两腿|成交持仓与合约明细/);assert.match(html,/窗口内观测不足/);assert.doesNotMatch(html,/历史分位/);
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
 assert.deepEqual(option.series.map(s=>s.itemStyle.color),['#4e7fb9','#a48b59','#4e7fb9','#a48b59','#222222']);
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


async function sharedHooks(overrides={},file='charts.js',previews=false) {
 const source=(await readFile(new URL('../../../frontend/src/'+file,import.meta.url),'utf8')).replaceAll('import.meta.env.OBSERVE_PREVIEW',String(previews)).replaceAll('import.meta.env.INTRADAY_PREVIEW',String(previews)).replaceAll('import.meta.env.VITE_HOME_URL','undefined').replaceAll('import.meta.env.BASE_URL',"'/'");
 const code=ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 const slots=[],effects=[],module={exports:{}};let index=0;
 const hooks={...React,useState:initial=>{const i=index++;slots[i]||={value:typeof initial==='function'?initial():initial};return [slots[i].value,next=>{slots[i].value=typeof next==='function'?next(slots[i].value):next;}];},
  useRef:value=>slots[index++]||=( {current:value} ),
  useEffect:(effect,deps)=>{const i=index++,old=slots[i];if(!old||!deps||deps.some((v,j)=>!Object.is(v,old.deps[j])))effects.push(()=>{old?.cleanup?.();slots[i]={deps,cleanup:effect()};});}};
 new Function('module','exports','require',code)(module,module.exports,id=>id==='react'?hooks:overrides[id]||(id.startsWith('react/')?sharedRequire(id):{}));
 return {render:(name,...args)=>{index=0;const value=module.exports[name](...args);effects.splice(0).forEach(fn=>fn());return value;},unmount:()=>slots.forEach(slot=>slot.cleanup?.())};
}
const settleHooks=()=>new Promise(resolve=>setImmediate(resolve));

test('module loading paints the shell, ignores a departed module and isolates import failures',async t=>{
 const originals=Object.fromEntries(['location','history','window','document','matchMedia'].map(key=>[key,globalThis[key]]));
 t.after(()=>Object.assign(globalThis,originals));
 globalThis.location={pathname:'/futures/',search:'',origin:'http://localhost'};
 globalThis.history={pushState:(_a,_b,url)=>{globalThis.location.pathname=url.pathname;globalThis.location.search=url.search;}};
 globalThis.window={addEventListener(){},removeEventListener(){},scrollTo(){}};
 globalThis.document={addEventListener(){},removeEventListener(){},getElementById:()=>({textContent:JSON.stringify({seats:'assets/seats.js',futures:'assets/futures.js',options:'assets/options.js'})})};
 globalThis.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
 let finishFutures;
 const future=()=>null,seats=()=>null;
 const warmed=[];
 const h=await sharedHooks({'./transport.js':{warmNavigation:urls=>{warmed.push(urls);return()=>{};}},'./version-check.js':{watchVersion:()=>()=>{}},'./preferences.js':{selectedProduct:()=>null},
  './futures/App.jsx':new Promise(resolve=>finishFutures=()=>resolve({App:future})), './seats/Dashboard.tsx':{default:seats},
  './Options.jsx':{then:(_resolve,reject)=>reject(Error('asset unavailable'))}},'Shell.jsx');
 const nodes=node=>!node||typeof node!=='object'?[]:[node,...React.Children.toArray(node.props?.children).flatMap(nodes)];
 const boundary=tree=>nodes(tree).find(node=>node.type?.name==='ModuleErrorBoundary');
 const navigate=id=>{
  const tree=h.render('default'),all=nodes(tree);
  all.find(node=>node.type==='details').props.ref.current={open:false,querySelector:()=>({focus(){}})};
  all.find(node=>node.type==='a'&&node.props.href==='/'+id+'/').props.onClick({preventDefault(){}});
  return h.render('default');
 };
 assert.ok(nodes(h.render('default')).some(node=>node.props?.['aria-label']==='站点导航'));
 navigate('seats');await settleHooks();
 assert.ok(nodes(h.render('default')).some(node=>node.type===seats));
 assert.deepEqual(warmed.at(-1),['/assets/futures.js','/assets/options.js','https://mingjiec.com/']);
 finishFutures();await settleHooks();assert.ok(nodes(h.render('default')).some(node=>node.type===seats));
 navigate('options');await settleHooks();const failed=h.render('default');
 assert.equal(boundary(failed).props.error.message,'asset unavailable');
 assert.match(renderToStaticMarkup(failed),/页面暂时无法加载/);
 navigate('futures');await settleHooks();assert.ok(nodes(h.render('default')).some(node=>node.type===future));
 h.unmount();
});

test('board trigger pins the menu without navigation; hover, outside, Escape and child links retain their roles',async t=>{
 const originals=Object.fromEntries(['location','history','window','document'].map(k=>[k,globalThis[k]]));t.after(()=>Object.assign(globalThis,originals));
 const listeners={},pushed=[];
 globalThis.location={pathname:'/futures/',search:'',origin:'http://localhost'};
 globalThis.history={pushState:(_a,_b,url)=>pushed.push(url.pathname)};
 globalThis.window={addEventListener(){},removeEventListener(){},scrollTo(){}};
 globalThis.document={addEventListener:(name,fn)=>listeners[name]=fn,removeEventListener(){},getElementById:()=>null};
 const h=await sharedHooks({'./transport.js':{warmNavigation:()=>()=>{}},'./version-check.js':{watchVersion:()=>()=>{}},'./preferences.js':{selectedProduct:()=>null},'./futures/App.jsx':{App:()=>null},'./seats/Dashboard.tsx':{default:()=>null}},'Shell.jsx');
 const nodes=n=>!n||typeof n!=='object'?[]:[n,...React.Children.toArray(n.props?.children).flatMap(nodes)];
 const all=nodes(h.render('default')),menu=all.find(n=>n.type==='details'),summary=all.find(n=>n.type==='summary');
 const element={open:false,contains:target=>target==='inside',querySelector:()=>({focus(){}})};menu.props.ref.current=element;
 assert.equal(nodes(summary).some(n=>n.type==='a'),false);
 menu.props.onPointerEnter({pointerType:'mouse'});assert.equal(element.open,true);
 menu.props.onPointerLeave({pointerType:'mouse'});assert.equal(element.open,false);
 summary.props.onClick({preventDefault(){}});menu.props.onPointerLeave({pointerType:'mouse'});
 assert.equal(element.open,true);assert.deepEqual(pushed,[]);
 listeners.pointerdown({target:'inside'});assert.equal(element.open,true);
 listeners.pointerdown({target:'outside'});assert.equal(element.open,false);
 menu.props.onPointerEnter({pointerType:'touch'});assert.equal(element.open,false);
 summary.props.onClick({preventDefault(){}});menu.props.onKeyDown({key:'Escape',preventDefault(){}});assert.equal(element.open,false);
 summary.props.onClick({preventDefault(){}});all.find(n=>n.type==='a'&&n.props.href==='/seats/').props.onClick({preventDefault(){}});
 assert.deepEqual(pushed,['/seats/']);assert.equal(element.open,false);h.unmount();await settleHooks();
});

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
 let created=0,disposed=0,draws=0,resized=0,resize;
 globalThis.IntersectionObserver=class{constructor(callback){this.callback=callback;}observe(){queueMicrotask(()=>this.callback([{isIntersecting:true}]));}disconnect(){}};
 globalThis.ResizeObserver=class{constructor(callback){resize=callback;}observe(){resize();}disconnect(){}};
 try{
  const h=await sharedHooks({'./chart-engine.js':{init:()=>{created++;return {dispose:()=>disposed++,getWidth:()=>100,getHeight:()=>80,resize(){resized++;}};}}});
  const ref={current:{clientWidth:100,clientHeight:80}},draw=()=>draws++;
  h.render('useChart',ref,draw,[1]);await settleHooks();
  assert.equal(ref.current.ariaBusy,'false');
  h.render('useChart',ref,draw,[2]);await settleHooks();
  assert.equal(created,1);assert.equal(disposed,0);assert.equal(draws,2);assert.equal(resized,0);
  ref.current.clientWidth=120;resize();assert.equal(resized,1);
  h.render('useChart',ref,draw,[2]);assert.equal(draws,2);
  ref.current=null;h.render('useChart',ref,draw,[2]);assert.equal(disposed,1);
  ref.current={clientWidth:100,clientHeight:80};h.render('useChart',ref,draw,[3]);await settleHooks();
  assert.equal(created,2);h.unmount();assert.equal(disposed,2);
 }finally{globalThis.IntersectionObserver=oldIntersection;globalThis.ResizeObserver=oldResize;}
});


test('single published inventory observation is visible in both charts without invented changes',async()=>{
 const source=await readFile(new URL('../src/HistoryPanel.jsx',import.meta.url),'utf8');
 const code=ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 const module={exports:{}},draws=[],data={series:[{id:'SI_W',kind:'仓单',name:'工业硅仓单',unit:'手',points:[{date:'2026-10-29',value:31327}]}]};
 new Function('module','exports','require',code)(module,module.exports,id=>id.endsWith('/SpreadMatrix.jsx')?matrixModule.exports:id.endsWith('/charts.js')?{...createRequire(import.meta.url)(id),usePreference:(_key,value)=>[value,()=>{}],useResource:()=>({data}),useChart:(_ref,draw)=>draws.push(draw)}:id.startsWith('react')?sharedRequire(id):createRequire(new URL('../src/HistoryPanel.jsx',import.meta.url))(id));
 renderToStaticMarkup(React.createElement(module.exports.InventoryPanel,{product:'SI',end:'',controls:()=>null}));
 const options=draws.map(draw=>{let option;draw({setOption:value=>option=value,on(){},off(){}});return option;});
 assert.equal(options.length,2);
 assert.equal(options[0].yAxis.axisLabel.formatter(12345.6),'12,345.6');
 for(const option of options){assert.equal(option.series[0].showSymbol,true);assert.equal(option.series[0].symbolSize,6);assert.deepEqual(option.series[0].data.filter(Number.isFinite),[31327]);}
 assert.equal(options[0].series[0].showAllSymbol,true);
 assert.deepEqual(options[1].series.slice(1).map(s=>s.data),[[null],[null]]);
});


test('contract legend highlights every segment without hiding any contract',async()=>{
 const source=await readFile(new URL('../src/HistoryPanel.jsx',import.meta.url),'utf8');
 const code=ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 const module={exports:{}},draws=[];
 new Function('module','exports','require',code)(module,module.exports,id=>id.endsWith('/SpreadMatrix.jsx')?matrixModule.exports:id.endsWith('/charts.js')?{...createRequire(import.meta.url)(id),useChart:(_ref,draw)=>draws.push(draw)}:id.startsWith('react')?sharedRequire(id):createRequire(new URL('../src/HistoryPanel.jsx',import.meta.url))(id));
 const result={mode:'overview',metric:'price',axis:['2026-10-01','2026-10-02'],lines:['IM2611','IM2612','IM2703'].map((label,i)=>({label,points:[{x:'2026-10-01',trade_date:'2026-10-01',value:100+i}]}))};
 renderToStaticMarkup(React.createElement(module.exports.HistoryChart,{result,activeContract:'IM2612',secondaryContract:'IM2703'}));
 const options=[];let select;
 draws.pop()({setOption:o=>options.push(o),off(){},on:(_event,handler)=>{select=handler;}});
 select({name:'IM2611',selected:{IM2611:false}});
 assert.equal(options[0].yAxis.axisLabel.formatter(12345.67),'12345.67');
 assert.ok(Object.values(options[1].legend.selected).every(Boolean));
 assert.equal(options[1].series[0].lineStyle.width,3);
 select({name:'IM2611',selected:{IM2611:false}});
 assert.equal(options[2].series[0].lineStyle.width,1.3);
 for(const value of ['Y+0 01-02','Y-1 12-31','Y+1 01-01'])assert.equal(options[0].xAxis.axisLabel.formatter(value),value.split(' ')[1]);
 assert.equal(options[0].xAxis.axisLabel.formatter('2026-10-01'),'2026-10-01');
 assert.equal(options[0].dataZoom[1].labelFormatter(1,'Y+0 01-02'),'01-02');
});


test('basis overlays current contracts on one date axis and retains missing prices',async()=>{
 const code=ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 const module={exports:{}},charts=[],draws=[],point=(trade_date,basis,price)=>({trade_date,reference_spread:basis,basis_ratio:basis/100,annualized_basis_ratio:basis/100,reference_price:100,futures_price:price});
 const a={series:[point('2026-09-01',1,99),point('2026-09-02',2,98)],matched:2,latest:point('2026-09-02',2,98)};
 const b={series:[point('2026-09-02',4,96)],matched:1,latest:point('2026-09-02',4,96)};
 const data={...a,contracts:['IM2611','IM2612','IM2703'],contract_positions:{IM2611:10,IM2612:100,IM2703:80},symbol:'IM2612',status:'index_close_basis',meta:{as_of:'2026-09-02'},reference:{name:'中证1000'},comparisons:{IM2611:b,IM2612:a,IM2703:b}};
 new Function('module','exports','require',code)(module,module.exports,id=>id.endsWith('/SpreadMatrix.jsx')?matrixModule.exports:id.endsWith('/charts.js')?{...createRequire(import.meta.url)(id),usePreference:(k,v)=>[k==='futures.basis.scope'?'contract':v,()=>{}],useResource:url=>url.includes('history=true')?{data:null}:{data},useChart:(_ref,draw)=>draws.push(draw)}:id.startsWith('react')?sharedRequire(id):createRequire(new URL('../src/MarketPanels.jsx',import.meta.url))(id));
 const html=renderToStaticMarkup(React.createElement(module.exports.BasisPanel,{product:'IM',Chart:props=>{charts.push(props);return null;}}));
 assert.match(html,/<h2>基差率季节图<\/h2>/);assert.match(html,/<h2>基差率走势图<\/h2>/);
 assert.ok(html.indexOf('基差率季节图')<html.indexOf('基差率走势图')&&html.indexOf('基差率走势图')<html.indexOf('上市合约'));
 assert.deepEqual(charts[0].lines,[['IM2612_ratio','IM2612'],['IM2703_ratio','IM2703'],['IM2611_ratio','IM2611']]);
 assert.equal(charts[0].rows[0].IM2703,undefined);
 assert.equal(charts[0].rows[1].IM2703,4);
 assert.equal(charts[0].focus,'IM2612');
 assert.equal(charts[0].percent,true);
 assert.equal(charts[0].colors.IM2612,'#4e7fb9');assert.equal(charts[0].colors.IM2703,'#a48b59');
 let option;draws[0]({setOption:o=>option=o});
 assert.deepEqual(option.series.map(s=>s.name),['中证1000','IM2612','基差率']);
 assert.deepEqual(option.series.map(s=>s.data),[[100,100],[99,98],[1,2]]);
 assert.equal(option.series[2].yAxisIndex,1);assert.equal(option.series[2].type,'bar');
 assert.equal(option.series[1].itemStyle.color,charts[0].colors.IM2612);
});

test('basis comparisons preserve gaps and dates; frequency distribution includes every valid observed day before cutoff',()=>{
 const p=(trade_date,days_to_expiry,value)=>({trade_date,days_to_expiry,basis_ratio:value,reference_spread:value==null?null:value*100,annualized_basis_ratio:value==null?null:value*5});
 const data={lines:[{year:2025,label:'IM2512',points:[p('2024-12-01',380,.03),p('2025-10-01',78,null),p('2025-10-02',77,0),p('2025-10-03',76,.02)]},{year:2026,label:'IM2612',points:[p('2026-10-02',77,.01)]}]};
 const calendar=basisHistory(data,'basis_ratio','calendar'),expiry=basisHistory(data,'basis_ratio','expiry');
 assert.equal(calendar.lines[0].points[0].x,'Y-1 12-01');assert.equal(calendar.lines[0].points[1].value,null);
 assert.equal(calendar.lines[0].points[2].value,0);assert.equal(calendar.lines[0].points[3].value,2);
 assert.deepEqual(expiry.axis,['-380','-78','-77','-76']);
 const summary=basisDistribution(data,'basis_ratio',data.lines[1].points[0],'IM2612');
 assert.equal(summary.n,4);assert.equal(summary.percentile,37.5);assert.equal(summary.value,1);
 assert.equal(summary.bins.reduce((n,b)=>n+b.n,0),4);
 assert.equal(basisDistribution(data,'basis_ratio',null,'IM2612').percentile,null);
 const equal=basisDistribution({lines:[data.lines[0],{...data.lines[1],points:[p('2026-10-02',77,0)]}]},'basis_ratio',p('2026-10-02',77,0),'IM2612');
 assert.equal(equal.percentile,25);
});

test('dominant basis keeps its calendar and counts every observed daily rate across years and expiries',()=>{
 const point=(trade_date,value,days=45)=>({trade_date,basis_ratio:value,reference_spread:value*100,days_to_expiry:days,symbol:'IM'+trade_date.slice(2,4)+'12'});
 const current=point('2026-11-05',.01),data={continuous:true,latest:current,meta:{as_of:'2026-11-05'},lines:[{label:'2025',year:2025,points:[point('2025-03-05',10),point('2025-11-05',0),point('2025-11-06',.02),point('2025-11-07',10,100)]},{label:'2026',year:2026,points:[current]}]};
 const result=basisHistory(data,'basis_ratio','calendar');
 assert.equal(result.axis.length,366);assert.equal(result.axis[0],'01-01');assert.equal(result.axis.at(-1),'12-31');
 assert.equal(result.current.label,'2026');assert.equal(result.current.point.value,1);assert.equal(result.current.point.x,'11-05');
 assert.equal(result.colors['2026'],'#4e7fb9');
 const distribution=basisDistribution(data,'basis_ratio',current,'IM2612');
 assert.equal(distribution.n,5);assert.equal(distribution.percentile,30);
 assert.equal(distribution.bins.reduce((n,b)=>n+b.n,0),5);
 data.lines[1].points.push(point('2026-12-01',-.5));
 assert.equal(basisDistribution(data,'basis_ratio',current).n,5);
});

test('price overlay uses recorded dominant prices and the selected raw or annual rate without changing units',()=>{
 const rows=[{trade_date:'2026-11-05',reference_price:100,futures_price:98,reference_spread:2,basis_ratio:.02,annualized_basis_ratio:.1,symbol:'IM2612'},
  {trade_date:'2026-11-06',reference_price:101,futures_price:null,reference_spread:null,basis_ratio:null,annualized_basis_ratio:null,symbol:null}];
 for(const [metric,label,value,unit] of [['basis_ratio','基差率',2,'%'],['annualized_basis_ratio','年化基差率',10,'%'],['reference_spread','基差',2,'']]){
  renderToStaticMarkup(React.createElement(BasisPrices,{rows,symbol:'主力',indexName:'中证1000',color:'#4e7fb9',metric}));
  let option;chartDraws.pop()({setOption:o=>{option=o;}});
  assert.deepEqual(option.series.map(s=>s.data),[[100,101],[98,null],[value,null]]);
  assert.equal(option.series[2].name,label);assert.equal(option.yAxis[1].axisLabel.formatter(2),'2'+unit);
  const tip=option.tooltip.formatter([{axisValue:'2026-11-05',dataIndex:0,seriesName:label,value}]);
  assert.match(tip,/2026-11-05 · IM2612/);assert.ok(tip.includes(label+'：'+value+unit));
 }
});

test('basis panel reuses one published history for prices, seasons and frequency with a one-year initial range',async()=>{
 const source=await readFile(new URL('../src/MarketPanels.jsx',import.meta.url),'utf8'),module={exports:{}},draws=[],groups=[],requests=[];
 const points=['2024-01-02','2025-11-05','2026-11-05'].map((trade_date,i)=>({trade_date,reference_price:100+i,futures_price:98+i,basis_ratio:2/(100+i),reference_spread:2,symbol:'IM2612'}));
 const latest=points.at(-1),data={status:'index_close_basis',symbol:'IM2612',contracts:['IM2612'],contract_positions:{IM2612:20},comparisons:{IM2612:{latest,series:[latest]}},meta:{as_of:latest.trade_date},reference:{name:'中证1000'},input_batch:'batch'};
 const history={continuous:true,latest,lines:points.map(p=>({label:p.trade_date.slice(0,4),year:Number(p.trade_date.slice(0,4)),points:[p]})),missing:[],meta:data.meta,input_batch:'batch'};
 const code=ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 new Function('module','exports','require',code)(module,module.exports,id=>id.endsWith('/SpreadMatrix.jsx')?matrixModule.exports:id.endsWith('/charts.js')?{...sharedRequire('./src/charts.js'),usePreference:(_key,value)=>[value,()=>{}],useResource:url=>{requests.push(url);return {data:url.includes('history=true')?history:data};},useChart:(_ref,draw,_deps,group)=>{draws.push(draw);if(group)groups.push(group);}}:id.startsWith('react')?sharedRequire(id):createRequire(new URL('../src/MarketPanels.jsx',import.meta.url))(id));
 const html=renderToStaticMarkup(React.createElement(module.exports.BasisPanel,{product:'IM',end:'',priceType:'close',revision:0,Chart:()=>null,HistoryChart:()=>null,controls:()=>null}));
 let option;draws[0]({setOption:o=>{option=o;}});
 assert.deepEqual(option.xAxis.data,points.map(p=>p.trade_date));assert.deepEqual(option.series[1].data,[98,99,100]);
 assert.equal(option.series[1].name,'主力');assert.deepEqual(groups[0].range,{startValue:'2025-11-05',endValue:'2026-11-05'});
 assert.equal(groups[0].byDate,true);assert.equal(requests.length,2);assert.match(requests[1],/continuous=true/);
 for(const title of ['基差率季节图','基差率走势图','上市合约','基差率频率分布'])assert.ok(html.includes(title));
 assert.match(html,/3 个观测/);
});

test('basis frequency colors follow the signed bin position without changing counts',()=>{
 const summary={bins:[{start:-2,end:0,n:4},{start:0,end:2,n:7}],value:0};
 renderToStaticMarkup(React.createElement(BasisDistribution,{summary,percent:true}));
 let option;chartDraws.pop()({setOption:o=>option=o});
 assert.deepEqual(option.series[0].data,[[-1,4],[1,7]]);
 const color=value=>option.series[0].itemStyle.color({value:[value,4]});
 assert.equal(color(-1),'#579981');assert.equal(color(1),'#bc706a');assert.equal(color(0),'#aebdce');
});

test('one basis selector keeps quotes, history, prices, distribution and contract focus on the same object',()=>{
 const module={exports:{}},preferences={'futures.basis.IM.symbol':'IM2703'},requests=[],draws=[],metrics=[],charts=[];
 const point=(symbol,basis)=>({trade_date:'2026-11-05',symbol,reference_price:100,futures_price:100-basis,reference_spread:basis,basis_ratio:basis/100,annualized_basis_ratio:basis/10});
 const mainPoint=point('IM2612',10),chosenPoint=point('IM2703',2),data={status:'index_close_basis',symbol:'IM2612',contracts:['IM2612','IM2703'],comparisons:{IM2612:{series:[mainPoint],latest:mainPoint},IM2703:{series:[chosenPoint],latest:chosenPoint}},meta:{as_of:'2026-11-05'},reference:{name:'中证1000'},input_batch:'batch'};
 let historyReady=true,selection;
 const code=ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 new Function('module','exports','require',code)(module,module.exports,id=>id.endsWith('/SpreadMatrix.jsx')?matrixModule.exports:id.endsWith('/charts.js')?{...sharedRequire('./src/charts.js'),
  usePreference:(key,value)=>[preferences[key]??value,next=>{preferences[key]=next;}],
  useResource:(url,_revision,scope)=>{requests.push({url,scope});const main=url.includes('continuous=true'),p=main?mainPoint:chosenPoint;return {data:!url.includes('history=true')?data:historyReady?{continuous:main,latest:main?p:undefined,symbol:p.symbol,lines:[{label:main?'2026':p.symbol,year:2026,points:[p]}],missing:[],meta:data.meta,input_batch:'batch'}:null};},
  Metric:props=>{metrics.push(props);return null;},useChart:(_ref,draw)=>draws.push(draw)
 }:id.startsWith('react')?sharedRequire(id):createRequire(new URL('../src/MarketPanels.jsx',import.meta.url))(id));
 const render=()=>{
  requests.length=draws.length=metrics.length=charts.length=0;
  renderToStaticMarkup(React.createElement(module.exports.BasisPanel,{product:'IM',end:'2026-11-05',priceType:'close',revision:0,Chart:props=>{charts.push(props);return null;},HistoryChart:()=>null,controls:fragment=>{selection=React.Children.toArray(fragment.props.children).filter(React.isValidElement);return fragment;}}));
  return draws.map(draw=>{let option;draw({setOption:o=>option=o});return option;});
 };
 for(const [value,expected] of [['main',mainPoint],['IM2703',chosenPoint],['main',mainPoint]]){
  if(selection)selection.find(e=>e.props['aria-label']==='基差合约').props.onChange({target:{value}});
  const options=render();
  assert.equal(requests.length,2);assert.equal(requests[0].scope,'IM');assert.ok(requests[1].url.includes(value==='main'?'continuous=true':'symbol=IM2703'));assert.match(requests[1].url,/end=2026-11-05/);
  assert.equal(metrics.find(m=>m.label==='基差').value,String(expected.reference_spread));assert.equal(metrics.find(m=>m.label===expected.symbol).value,String(expected.futures_price));
  assert.deepEqual(options[0].series[1].data,[expected.futures_price]);assert.equal(options[1].series[0].markLine.data[0].xAxis,expected.reference_spread);
  assert.equal(charts[0].focus,expected.symbol);assert.equal(charts[0].lines.length,2);
  assert.deepEqual(selection.map(e=>e.props['aria-label']),['基差合约','基差指标','基差历史跨度',...(value==='main'?[]:['基差对齐方式'])]);
 }
 historyReady=false;const options=render();
 assert.equal(metrics.find(m=>m.label==='基差').value,'—');assert.deepEqual(options[0].series[1].data,[]);
 assert.equal(requests[1].scope,'IM:main');
});

test('spread latest marker skips missing tails and retains a real zero without building a point list',async()=>{
 const text=await readFile(new URL('../src/SpreadPanel.jsx',import.meta.url),'utf8'),module={exports:{}},draws=[];
 let series;
 new Function('module','exports','require',ts.transpileModule(text,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText)(module,module.exports,id=>id.endsWith('/charts.js')?{formatPrice,usePreference:(_key,value)=>[value,()=>{}],useResource:()=>({data:{series,paired_count:series.filter(r=>r.spread!=null).length}}),useChart:(_ref,draw)=>draws.push(draw)}:sharedRequire(id));
 for(const values of [[null,0,null],[1,-2,null],[null,null]]){
  series=values.map((spread,i)=>({trade_date:'2026-11-0'+(i+1),spread}));
  const html=renderToStaticMarkup(React.createElement(module.exports.default,{data:{product:'IM',near:'IM2612',far:'IM2703',meta:{as_of:'2026-11-05'}},revision:0,controls:()=>null}));
  let option;draws.pop()({setOption:o=>option=o});
  const index=values.findLastIndex(v=>v!=null);
  assert.deepEqual(option.series[0].markPoint.data,index<0?[]:[{coord:[index,values[index]],value:values[index]}]);
  if(index>=0)assert.ok(html.includes('最新值'+values[index]));
 }
});

test('shared axis tooltips omit missing values and retain valid zero across scalar and coordinate series',()=>{
 const params=[null,undefined,'',NaN,Infinity,'-'].map(value=>({seriesName:'缺失',value,axisValue:'11-05'}));
 params.push({seriesName:'有效零',value:0},{seriesName:'坐标零',value:[6500,0]},{seriesName:'隐波',value:[6500,30]});
 const text=axisTooltip(params,v=>v+'%');
 assert.doesNotMatch(text,/缺失|—|NaN/);assert.match(text,/有效零：0%/);assert.match(text,/坐标零：0%/);assert.match(text,/隐波：30%/);
 assert.equal(axisTooltip(params.slice(0,6)), '');
});


test('explicit contract colors stay fixed when the index is first and focus changes',async()=>{
 const source=await readFile(new URL('../src/App.jsx',import.meta.url),'utf8');
 const code=ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 const module={exports:{}},draws=[],require=createRequire(new URL('../src/App.jsx',import.meta.url));
 new Function('module','exports','require',code)(module,module.exports,id=>id.endsWith('/SpreadMatrix.jsx')?matrixModule.exports:id.endsWith('/charts.js')?{...sharedRequire('./src/charts.js'),useChart:(_ref,draw)=>draws.push(draw)}:id.endsWith('.jsx')?{}:id.startsWith('react')?sharedRequire(id):require(id));
 const props={rows:[{trade_date:'2026-10-30',index:100,near:99,far:null}],lines:[['index','中证1000'],['near','IM2612'],['far','IM2703']],colors:{'中证1000':'#505c6b',IM2612:'#4e7fb9',IM2703:'#a48b59'}};
 for(const focus of ['IM2612','IM2703']){
  renderToStaticMarkup(React.createElement(module.exports.Chart,{...props,focus}));
  let option;draws.pop()({setOption:o=>{option=o;}});
  assert.deepEqual(option.series.map(s=>s.itemStyle.color),['#505c6b','#4e7fb9','#a48b59']);
  assert.equal(option.series.find(s=>s.name===focus).lineStyle.width,2.8);
  assert.deepEqual(option.series[2].data,[null]);
 }
});


test('observation cycle compression retains true returns and allocation uses a separate price axis',async()=>{
 const source=await readFile(new URL('../../../frontend/src/Observe.jsx',import.meta.url),'utf8');
 const code=ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 const module={exports:{}},draws=[];
 new Function('module','exports','require',code)(module,module.exports,id=>id.endsWith('/SpreadMatrix.jsx')?matrixModule.exports:id.endsWith('/charts.js')?{...sharedRequire('./src/charts.js'),useChart:(_ref,draw)=>draws.push(draw)}:id==='./preferences.js'?{}:sharedRequire(id));
 const points=[{trade_date:'2020-01-01',value:0},{trade_date:'2020-01-02',value:4}];
 renderToStaticMarkup(React.createElement(module.exports.ContextChart,{cycle:true,data:{series:points,cycles:[{start:'2020-01-01',end:'2020-01-02',direction:1,points}]}}));
 let option;draws.pop()({setOption:o=>{option=o;}});
 assert.ok(Math.abs(option.series[0].data[1]-Math.log10(5))<1e-12);
 assert.equal(option.series[0].markPoint.label.formatter,'2020-01\n400%');
 assert.equal(option.yAxis.min,-1);assert.equal(option.yAxis.max,1);
 assert.match(option.tooltip.formatter([{seriesIndex:0,value:Math.log10(5),axisValue:'2020-01-02'}]),/400%/);
 points[1].value=12.44;
 renderToStaticMarkup(React.createElement(module.exports.ContextChart,{cycle:true,data:{series:points,cycles:[{start:'2020-01-01',end:'2020-01-02',direction:1,points}]}}));
 draws.pop()({setOption:o=>{option=o;}});
 assert.ok(option.yAxis.max>=option.series[0].data[1]);
 assert.equal(option.yAxis.min,-option.yAxis.max);
 renderToStaticMarkup(React.createElement(module.exports.ContextChart,{data:{series:[{trade_date:'2020-01-01',ratio:.01,index_close:4000,net_assets:1e9,float_market_cap:1e11}]}}));
 draws.pop()({setOption:o=>{option=o;}});
 assert.equal(option.series[1].yAxisIndex,1);
 assert.deepEqual(option.series[1].data,[4000]);
 assert.deepEqual(option.series.map(s=>s.itemStyle.color),['#a48b59','#4e7fb9']);
 renderToStaticMarkup(React.createElement(module.exports.ContextChart,{flow:true,data:{funds:[{group:'individual'},{group:'institution'}],series:[{trade_date:'2026-09-07',individual:0,institution:null},{trade_date:'2026-09-08',individual:3,institution:-2}]}}));
 draws.pop()({setOption:o=>{option=o;}});
 assert.deepEqual(option.series.map(s=>s.data),[[0,3],[null,-2]]);
 assert.deepEqual(option.series.map(s=>s.itemStyle.color),['#4e7fb9','#a48b59']);
 assert.equal(option.series[0].connectNulls,false);
 assert.match(option.tooltip.formatter([{seriesName:'个人为主',value:3,axisValue:'2026-09-08'}]),/个人为主：3亿元/);
});

test('ETF funds share one page and chart height, preserving monthly gaps and separate data dates',async()=>{
 const source=await readFile(new URL('../../../frontend/src/Observe.jsx',import.meta.url),'utf8');
 const code=ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 const module={exports:{}},requests=[],draws=[],originalLocation=globalThis.location;
 const series=Array.from({length:21},(_,i)=>({trade_date:`2026-10-${String(i+1).padStart(2,'0')}`,individual:i*7,institution:-i*11,individual_daily:2,institution_daily:-3}));
 const flow={series,funds:[{group:'individual'},{group:'institution'},{group:'unclassified'}],coverage:{included_flow:1406},meta:{currency_assumed:true}};
 const allocation={series:[{trade_date:'2026-10-21',ratio:.01,index_close:4000},{trade_date:'2026-10-22',ratio:null,index_close:4010}],funds:[]};
 new Function('module','exports','require',code)(module,module.exports,id=>id==='./charts.js'?{...sharedRequire('./src/charts.js'),usePreference:(_k,v)=>[v,()=>{}],useResource:url=>{requests.push(url);return {data:url.includes('view=allocation')?allocation:flow};},useChart:(_ref,draw)=>draws.push(draw)}:id==='./preferences.js'?{}:sharedRequire(id));
 try{
  for(const mode of ['cumulative','recent','allocation']){
   globalThis.location={search:'?view=funds&fund_view='+mode+'&end=2026-10-30'};requests.length=draws.length=0;
   const html=renderToStaticMarkup(React.createElement(module.exports.default));
   assert.equal(requests.length,2);assert.equal(draws.length,3);assert.match(requests[0],/view=flow&end=2026-10-30/);assert.match(requests[1],/view=allocation&end=2026-10-30/);
   assert.doesNotMatch(html,/资金视角|近20日|未分类|<table/);assert.equal((html.match(/height:380px/g)||[]).length,3);
   assert.match(html,/规模占比<\/span><strong>1%/);assert.match(html,/2026-10-21/);
   assert.ok(html.indexOf('头部沪深300ETF规模占比')<html.indexOf('累计净申赎估算'));assert.ok(html.indexOf('累计净申赎估算')<html.indexOf('月度净申赎'));
    assert.match(html,/人民币假设/);assert.ok(html.includes('个人为主ETF</button><strong>140'));assert.match(html,/末月截至 2026-10-21/);
    let option;draws[2]({setOption:o=>option=o});
    assert.deepEqual(option.series.map(s=>s.type),['bar','bar']);assert.deepEqual(option.series.map(s=>s.data),[[42],[-63]]);
    assert.deepEqual(option.xAxis.data,['2026-10']);assert.equal(option.xAxis.boundaryGap,true);
    assert.match(option.tooltip.formatter([{dataIndex:0,axisValue:'2026-10',seriesName:'个人为主',value:42},{dataIndex:0,axisValue:'2026-10',seriesName:'机构为主',value:-63}]),/2026-10 · 截至 2026-10-21\n个人为主：42亿元\n机构为主：-63亿元/);
  }
  const allocationRows=allocation.series;allocation.series=[];draws.length=0;
  const partial=renderToStaticMarkup(React.createElement(module.exports.default));
  assert.equal(draws.length,2);assert.match(partial,/ETF累计净申赎估算/);assert.match(partial,/暂无规模占比数据/);allocation.series=allocationRows;
  const monthly=module.exports.fundMonthlyView({...flow,series:[
   {trade_date:'2025-12-30',individual_daily:10,institution_daily:0},
   {trade_date:'2025-12-31',individual_daily:-3,institution_daily:0},
   {trade_date:'2026-01-02',individual_daily:null,institution_daily:4},
   {trade_date:'2026-01-05',individual_daily:6,institution_daily:-2},
   {trade_date:'2026-02-02',individual_daily:0,institution_daily:null}
  ]});
  assert.deepEqual(monthly.series,[
   {trade_date:'2025-12',period_end:'2025-12-31',individual:7,institution:0},
   {trade_date:'2026-01',period_end:'2026-01-05',individual:null,institution:2},
   {trade_date:'2026-02',period_end:'2026-02-02',individual:0,institution:null}
  ]);
  assert.equal(flow.series.length,21);assert.equal(flow.series[20].individual,140);
  const history={...flow,series:[
   {trade_date:'2020-07-01',individual:null,institution:null,individual_daily:null,institution_daily:null},
   {trade_date:'2020-07-02',individual:7,institution:8,individual_daily:7,institution_daily:8},
   {trade_date:'2020-08-03',individual:10,institution:9,individual_daily:3,institution_daily:1},
   {trade_date:'2020-08-04',individual:10,institution:9,individual_daily:0,institution_daily:0}
  ]},scale={series:[{trade_date:'2013-03-25',ratio:.01},{trade_date:'2020-08-03',ratio:.02},{trade_date:'2020-08-04',ratio:null}]};
  const aligned=module.exports.fundsView(history,scale);
  assert.deepEqual(aligned.flow.series.map(p=>p.trade_date),['2020-08-03','2020-08-04']);
  assert.equal(aligned.flow.series[0].individual,10);assert.equal(aligned.monthly.series[0].trade_date,'2020-08');
  assert.equal(aligned.monthly.series[0].individual,3);assert.equal(aligned.allocation.series[0].trade_date,'2020-08-03');
  assert.equal(aligned.allocation.series[1].ratio,null);assert.equal(history.series.length,4);assert.equal(scale.series.length,3);
  assert.equal(module.exports.fundsView(history,null).flow,history);
 }finally{globalThis.location=originalLocation;}
});

test('intraday is independent of daily data, history samples and refresh controls',async()=>{
 const source=await readFile(new URL('../../../frontend/src/Intraday.jsx',import.meta.url),'utf8');
 assert.doesNotMatch(source,/useResource|RefreshButton|api\/futures|api\/options|intraday-preview|ricequant/i);
 assert.match(source,/实时行情/);
});

test('stock-bond states mark both tails and expose daily percentile ranks without changing returns',async()=>{
 const source=await readFile(new URL('../../../frontend/src/Observe.jsx',import.meta.url),'utf8');
 const code=ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 const module={exports:{}},draws=[],originalLocation=globalThis.location;
 const series=[.02,-.02,0,null].map((v,i)=>({trade_date:`2026-10-${i+1}`,stock_symbol:'IM2612',stock_close:6000+i,bond_return20:v,bond_p90:.01,bond_p10:-.01,bond_percentile:[95,5,50,null][i]}));
 new Function('module','exports','require',code)(module,module.exports,id=>id==='./charts.js'?{...sharedRequire('./src/charts.js'),usePreference:(_k,v)=>[v,()=>{}],useResource:()=>({data:{product:'IM',bond:'T',series}}),useChart:(_ref,draw)=>draws.push(draw)}:id==='./preferences.js'?{}:sharedRequire(id));
 try{
  globalThis.location={search:'?view=stock&product=IM'};
  const html=renderToStaticMarkup(React.createElement(module.exports.default));
  assert.match(html,/历史分位/);assert.match(html,/偏强1日 · 偏弱1日/);assert.equal(draws.length,2);
  let option;draws[0]({setOption:o=>option=o});
  assert.deepEqual(option.legend.data,['股指主力收盘','国债偏强','国债偏弱']);
  assert.deepEqual(option.series.slice(1).map(s=>s.data),[[6000,null,null,null],[null,6001,null,null]]);
  assert.match(option.tooltip.formatter([{dataIndex:1}]),/历史分位：5%\n国债偏弱/);
  draws[1]({setOption:o=>option=o});assert.deepEqual(option.series[0].data,[.02,-.02,0,null]);
  assert.deepEqual(option.legend.data,['20日涨跌幅','90%分位','10%分位']);
 }finally{globalThis.location=originalLocation;}
});

test('stock-bond overlay keeps two raw index levels, shared dates and missing observations',async()=>{
 const source=await readFile(new URL('../../../frontend/src/Observe.jsx',import.meta.url),'utf8');
 const code=ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 const module={exports:{}},draws=[],originalLocation=globalThis.location;
 const data={assets:[{key:'stock',name:'沪深300全收益',color:'#4e7fb9'},{key:'fund',name:'中证非纯债基',color:'#a48b59'},{key:'ordinary',name:'其他'}],series:[{trade_date:'2026-09-01',stock:5000,fund:null},{trade_date:'2026-09-02',stock:5100,fund:2000},{trade_date:'2026-09-03',stock:5200,fund:null},{trade_date:'2026-09-04',stock:5300,fund:2050},{trade_date:'2026-09-05',stock:5400,fund:null}]};
 new Function('module','exports','require',code)(module,module.exports,id=>id==='./charts.js'?{...sharedRequire('./src/charts.js'),usePreference:(_k,v)=>[v,()=>{}],useResource:()=>({data}),useChart:(_ref,draw)=>draws.push(draw)}:id==='./preferences.js'?{}:sharedRequire(id));
 const {comparisonView,ComparisonObserve}=module.exports,result=comparisonView(data);
 assert.deepEqual(result.lines.map(a=>a.key),['stock','fund']);
 assert.equal(result.start,'2026-09-02');assert.equal(result.end,'2026-09-04');
 assert.equal(result.series[0].stock,5100);assert.equal(result.series[0].fund,2000);
 assert.equal(result.series[1].fund,null);assert.equal(result.series[2].stock,5300);
 assert.deepEqual(comparisonView({assets:data.assets,series:[]}).series,[]);
 try{
  globalThis.location={search:'?view=compare&end=2026-10-30'};
  const html=renderToStaticMarkup(React.createElement(ComparisonObserve));
  assert.doesNotMatch(html,/<select|相对强弱|滚动相关|滚动一年|区间回撤|起点100/);
  assert.match(html,/沪深300与债基/);assert.doesNotMatch(html,/指数点位/);
  let option;draws.pop()({setOption:o=>{option=o;}});
  assert.deepEqual(option.series.map(s=>s.name),['沪深300全收益','中证非纯债基']);
  assert.deepEqual(option.series[0].data,[5100,5200,5300]);
  assert.deepEqual(option.series[1].data,[2000,null,2050]);
  assert.equal(option.series[1].connectNulls,false);
  assert.deepEqual(option.series.map(s=>s.yAxisIndex),[0,1]);
  assert.equal(option.yAxis.length,2);assert.equal(option.yAxis[0].name,'沪深300');assert.equal(option.yAxis[1].name,'债基');
  assert.equal(option.yAxis[0].min,5088);assert.equal(option.yAxis[0].max,5312);
  assert.deepEqual(result.anchors,[]);assert.equal(result.axisRatio,2.55);assert.equal(result.axisOffset,0);
  assert.equal(option.yAxis[1].min,5088/2.55);assert.equal(option.yAxis[1].max,5312/2.55);
  const position=(value,axis)=>(value-axis.min)/(axis.max-axis.min);
  assert.ok(Math.abs(position(5100,option.yAxis[0])-position(2000,option.yAxis[1]))<1e-12);
  const points=[{trade_date:'2014-06-01',stock:4000,fund:1000},{trade_date:'2014-06-02',stock:1500,fund:1000},{trade_date:'2019-01-03',stock:2500,fund:1500},{trade_date:'2024-02-02',stock:3500,fund:2000},{trade_date:'2024-09-13',stock:4500,fund:2500}];
  const anchored=comparisonView({assets:data.assets,series:points});
  assert.deepEqual(anchored.anchors.map(p=>p.trade_date),['2014-06-02','2019-01-03','2024-02-02','2024-09-13']);
  assert.equal(anchored.axisRatio,2);assert.equal(anchored.axisOffset,-500);
  assert.equal(comparisonView({assets:data.assets,series:points.slice(0,2)}).axisRatio,4);
  assert.equal(option.yAxis[1].axisLabel.color,'#a48b59');assert.equal(option.yAxis[1].splitLine.show,false);
  assert.match(option.tooltip.formatter([{value:5100,seriesName:'沪深300全收益',axisValue:'2026-09-02'}]),/5100/);
  renderToStaticMarkup(React.createElement(module.exports.ContextChart,{data:anchored,lines:anchored.lines,dualAxis:true}));
  draws.pop()({setOption:o=>{option=o;}});
  for(const p of anchored.anchors)assert.ok(Math.abs(position(p.stock,option.yAxis[0])-position(p.fund,option.yAxis[1]))<1e-12);
 }finally{globalThis.location=originalLocation;}
});

test('cycle valuation reuses line chart, preserves gaps and shows valuation without bond labels',async()=>{
 const source=await readFile(new URL('../../../frontend/src/Observe.jsx',import.meta.url),'utf8');
 const module={exports:{}},draws=[];
 new Function('module','exports','require',ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText)(module,module.exports,id=>id.endsWith('/charts.js')?{...sharedRequire('./src/charts.js'),useChart:(_ref,draw)=>draws.push(draw)}:id==='./preferences.js'?{}:sharedRequire(id));
 const data={metric:'pe_ttm',samples:[{trade_date:'2020-01-01',close:100}],series:[{trade_date:'2020-01-01',close:100,value:10,lower:88,deep:72,upper:140},{trade_date:'2020-01-02',close:110,value:null,lower:null,deep:null,upper:null}]};
 const lines=[{key:'close',name:'指数点位',color:'#4e7fb9'},{key:'lower',name:'低估参考',color:'#568f84',dashed:true},{key:'deep',name:'深估参考',color:'#b64d49',dashed:true},{key:'upper',name:'高估参考',color:'#a48b59',dashed:true}],zoomGroup=new Set();
 renderToStaticMarkup(React.createElement(module.exports.ContextChart,{data,lines,zoomGroup,label:'指数点位与估值参考',height:460}));
 let option;draws.pop()({setOption:o=>{option=o;}});
 assert.deepEqual(option.series.map(s=>s.data),[[100,110],[88,null],[72,null],[140,null]]);
 assert.deepEqual(option.series.map(s=>s.lineStyle.type),['solid','dashed','dashed','dashed']);
 assert.ok(option.series.every(s=>!s.endLabel.show&&!s.connectNulls&&s.yAxisIndex===0));
 assert.deepEqual(option.series[0].markPoint.data[0].coord,[1,110]);
 assert.deepEqual(option.series[2].markPoint.data[0].coord,[0,72]);
 assert.equal(option.yAxis.min({min:100,max:200}),94);assert.equal(option.yAxis.max({min:100,max:200}),206);
 assert.equal(option.series[0].lineStyle.width,2.6);assert.equal(option.dataZoom[0].start,0);
 assert.equal(option.dataZoom[0].end,100);assert.equal(option.yAxis.axisLabel.formatter(1234.56),'1235');
 assert.match(source,/key:'deep'.*color:'#b64d49'/);
 assert.match(option.tooltip.formatter([{seriesName:'指数点位',value:100,dataIndex:0,axisValue:'2020-01-01'}]),/PE\(TTM\)：10/);
 assert.doesNotMatch(option.tooltip.formatter([{seriesName:'指数点位',value:110,dataIndex:1,axisValue:'2020-01-02'},{seriesName:'低估参考',value:null,dataIndex:1}]),/低估参考|PE|—/);
 const view=module.exports.valuationView({series:[{trade_date:'2019-12-31',close:99},{trade_date:'2020-01-01',close:100},{trade_date:'2020-01-02',close:110}],valuation:{latest:{value:10},lower:8.8,deep:7.2,upper:14,series:[{trade_date:'2020-01-01',value:10}]}});
 assert.ok(Math.abs(view.series[0].lower-88)<1e-10);assert.equal(view.series[0].upper,140);
 assert.deepEqual(view.series.map(p=>p.trade_date),['2020-01-01','2020-01-02']);
 assert.deepEqual(view.series[1],{trade_date:'2020-01-02',close:110,value:null,lower:null,deep:null,upper:null});
 assert.equal(module.exports.valuationView({}),null);
 assert.match(source,/ContextChart data=\{valuation\}.*zoomGroup=\{zoomGroup\}/);
});


test('price formatting is ungrouped without changing quantity grouping or decimal precision',()=>{
 for(const value of [0,-12345.678,1234567.89,'12345.60'])assert.equal(formatPrice(value),Number(value).toLocaleString('zh-CN',{useGrouping:false,maximumFractionDigits:2}));
 for(const value of [null,undefined,'',NaN,Infinity])assert.equal(formatPrice(value),'—');
 assert.equal(formatPrice(12345.6,1,1),'12345.6');assert.equal(formatPrice(12345,1,1),'12345.0');
 assert.equal(sharedRequire('./src/intraday-live.js').formatNumber(1234567,0),'1,234,567');
 const drawsBefore=chartDraws.length;
 renderToStaticMarkup(React.createElement(BasisPrices,{rows:[{trade_date:'2026-11-06',reference_price:12345.67,futures_price:12300.6,basis_ratio:.00365}],symbol:'IM2612',indexName:'中证1000',metric:'basis_ratio'}));
 let option;chartDraws[drawsBefore]({setOption:o=>option=o});
 assert.equal(option.yAxis[0].axisLabel.formatter(12345.67),'12345.67');
 const text=option.tooltip.formatter([{seriesName:'中证1000',value:12345.67,dataIndex:0,axisValue:'2026-11-06'},{seriesName:'基差率',value:.365,dataIndex:0}]);
 assert.match(text,/12345.67/);assert.match(text,/0.37%/);assert.doesNotMatch(text,/12,345/);
});


test('observation defaults to its first tab and preserves explicit deep links without saved-tab redirection',async t=>{
 const previous={location:globalThis.location,history:globalThis.history};t.after(()=>Object.assign(globalThis,previous));
 for(const [query,expected] of [['','市场周期'],['?view=unknown','市场周期'],['?view=stock','国债强弱'],['?view=flow','ETF资金']]){
  globalThis.location={pathname:'/observe/',search:query};globalThis.history={replaceState(){}};
  const h=await sharedHooks({'./charts.js':{usePreference:()=>{throw Error('tab should not read saved preferences');}}},'Observe.jsx');
  const tree=h.render('default'),nodes=n=>!n||typeof n!=='object'?[]:[n,...React.Children.toArray(n.props?.children).flatMap(nodes)];
  assert.equal(nodes(tree).find(n=>n.type==='button'&&n.props['aria-pressed']).props.children,expected);h.unmount();
 }
});

test('main observation link resets a restored or currently active child view without reloading the shell',async t=>{
 const previous=Object.fromEntries(['location','history','window','document'].map(k=>[k,globalThis[k]]));t.after(()=>Object.assign(globalThis,previous));
 globalThis.location={pathname:'/observe/',search:'?view=stock',origin:'http://localhost'};
 globalThis.history={pushState:(_a,_b,url)=>{location.pathname=url.pathname;location.search=url.search;}};
 const listeners={};globalThis.window={addEventListener:(name,fn)=>listeners[name]=fn,removeEventListener(){},scrollTo(){}};
 globalThis.document={addEventListener(){},removeEventListener(){},getElementById:()=>null};
 const observe=()=>null,h=await sharedHooks({'./transport.js':{warmNavigation:()=>()=>{}},'./version-check.js':{watchVersion:()=>()=>{}},'./preferences.js':{selectedProduct:()=>null},'./Observe.jsx':{default:observe},'./Intraday.jsx':{default:()=>null}},'Shell.jsx',true);
 const nodes=n=>!n||typeof n!=='object'?[]:[n,...React.Children.toArray(n.props?.children).flatMap(nodes)];
 h.render('default');await settleHooks();
 const click=id=>{const all=nodes(h.render('default'));all.find(n=>n.type==='details').props.ref.current={open:false};all.find(n=>n.type==='a'&&n.props.href==='/'+id+'/').props.onClick({preventDefault(){}});};
 const before=nodes(h.render('default')).find(n=>n.type===observe).key;
 click('observe');assert.equal(location.search,'');assert.notEqual(nodes(h.render('default')).find(n=>n.type===observe).key,before);
 location.search='?view=funds';click('intraday');await settleHooks();h.render('default');click('observe');await settleHooks();
 assert.equal(location.pathname,'/observe/');assert.equal(location.search,'');
 h.render('default');await settleHooks();
 const key=nodes(h.render('default')).find(n=>n.type===observe).key;location.search='?view=stock';listeners.popstate();assert.notEqual(nodes(h.render('default')).find(n=>n.type===observe).key,key);h.unmount();
});
