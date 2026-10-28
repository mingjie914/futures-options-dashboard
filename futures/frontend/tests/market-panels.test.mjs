import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {transformWithEsbuild} from 'vite';
const sharedRequire=createRequire(new URL('../../../frontend/package.json',import.meta.url));
const React=sharedRequire('react'), {renderToStaticMarkup}=sharedRequire('react-dom/server');

const source=await readFile(new URL('../src/MarketPanels.jsx',import.meta.url),'utf8');
const {code}=await transformWithEsbuild(source,'MarketPanels.jsx',{jsx:'automatic',format:'cjs'});
const module={exports:{}};
new Function('module','exports','require',code)(module,module.exports,id=>id.startsWith('react')?sharedRequire(id):createRequire(import.meta.url)(id));
const {ScanPanel,CurvePanel,Trend,passesScan,RadarPanel,radarRows,BasisPanel}=module.exports;
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
test('basis panel distinguishes index close and CU reference scopes before data loads',()=>{
 const html=renderToStaticMarkup(React.createElement(BasisPanel,{product:'CU',priceType:'close',onProduct(){},Chart(){return null;}}));
 assert.match(html,/CU：周频参考价差/);
 assert.match(html,/股指：指数 − 期货/);
 assert.match(source,/正值：期货贴水；负值：期货升水/);
 assert.match(source,/重选当前活跃合约/);
});
test('contract rules remain scoped references',()=>{
 const terms={multiplier:200,tick_size:0.2,tick_value:40,settlement:'现金交割',source_url:'https://www.cffex.com.cn/zz1000/',reviewed_on:'2026-09-12',calendar_source_url:'https://www.cffex.com.cn/',expiry_rule:'第三个周五',as_of:'2026-09-11',contracts:[{symbol:'IM2609',expected_last_trade_date:'2026-09-18',calendar_days_remaining:7},{symbol:'IM2703',expected_last_trade_date:null,calendar_days_remaining:null}],warnings:['不用于年化']};
 const html=renderToStaticMarkup(React.createElement(module.exports.ContractTerms,{terms}));
 assert.match(html,/单腿每手一跳 40 元/);assert.match(html,/尚余 7 自然日/);
 assert.match(html,/非逐合约最终公告/);assert.match(html,/该年交易日历未核验/);
 assert.equal(renderToStaticMarkup(React.createElement(module.exports.ContractTerms,{})),'');

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
 assert.match(app,/start:result.start/);
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
 assert.equal(charts[0].rows[0].lag1,null);assert.equal(charts[0].lines.length,2);assert.equal(charts[1].bar,true);assert.equal(charts[2].integer,true);assert.match(html,/窗口内观测不足/);assert.doesNotMatch(html,/历史分位/);
});
