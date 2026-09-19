import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {transformWithEsbuild} from 'vite';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';

const source=await readFile(new URL('../src/MarketPanels.jsx',import.meta.url),'utf8');
const {code}=await transformWithEsbuild(source,'MarketPanels.jsx',{jsx:'automatic',format:'cjs'});
const module={exports:{}};
new Function('module','exports','require',code)(module,module.exports,createRequire(import.meta.url));
const {ScanPanel,CurvePanel,Trend,passesScan,RadarPanel,radarRows,BasisPanel}=module.exports;
test('raw candidate browser stays separate from business review and keeps unknown identity explicit',()=>{
 const html=renderToStaticMarkup(React.createElement(module.exports.FundamentalCandidates,{revision:0,Chart(){return null;}}));
 assert.match(html,/候选原始序列 · 同码属性核验/);
 assert.match(html,/不进入基差、雷达或回测/);
 assert.match(html,/未提供属性证据时，名称、单位与正式频率仍未知/);
 assert.match(source,/候选版本已变化/);
 assert.match(source,/未自动选择或统一口径/);
 assert.doesNotMatch(html,/已确认|交易建议/);
 assert.match(source,/d\.terminal_metadata\?\.metadata_sha256!==catalog\.terminal_metadata\?\.metadata_sha256/);
 assert.match(source,/期货序列 · 非独立现货/);
 assert.match(source,/不是本地取数水位/);
 assert.match(source,/单位未核验/);
});
test('EDB update status distinguishes replay, request cutoff and pre-check waterlines',()=>{
 const record={state:'completed',label:'隔离检查完成（非生产发布）',checked_at:'2026-09-12T05:22:40+00:00',requested_end:'2026-09-11',executed:true,network_fetch_enabled:false,counts:{pending_requests:0,release_check_requests:3,awaiting_release:null},waterlines:[{code:'S5715204',date:'2026-07-31'}],checkpoint:{label:'隔离检查完成（非生产发布）',requested_end:'2026-09-11',lock_present:false},issues:[]};
 const html=renderToStaticMarkup(React.createElement(module.exports.FundamentalUpdateStatus,{record}));
 for(const text of ['隔离检查完成','保存结果重放，未启用取数','不是指标取数或上游发布时间','不是最新观测日期','S5715204','2026-07-31','历史请求缺口：0','检查前待查发布区间：3','等待发布记录：未知','低频水位落后请求截止，不自动视为断更'])assert.ok(html.includes(text),text);
 assert.doesNotMatch(html,/<button|数据已全部更新/);
});
test('planning does not hide a failed execution checkpoint and locked is not running',()=>{
 const record={label:'仅生成计划',executed:false,counts:{},waterlines:[],checkpoint:{label:'上次执行失败',lock_present:true},issues:['运行记录含错误，请查看本地状态文件']};
 const html=renderToStaticMarkup(React.createElement(module.exports.FundamentalUpdateStatus,{record}));
 for(const text of ['仅计划，未执行','上次执行失败','锁文件存在，不代表进程仍存活','时间未核验','role="alert"'])assert.ok(html.includes(text),text);
});
test('update record errors and loading do not claim success or trigger ingestion',()=>{
 assert.match(renderToStaticMarkup(React.createElement(module.exports.FundamentalUpdateStatus,{})),/读取基本面独立更新记录/);
 const html=renderToStaticMarkup(React.createElement(module.exports.FundamentalUpdateStatus,{error:'service unavailable'}));
 assert.match(html,/role="alert"/);assert.match(html,/不触发取数/);assert.doesNotMatch(html,/检查完成/);
});
test('radar displays authoritative objects without reclassifying product summary',()=>{
 const objects=[{key:'IM:IM2610/IM2612',product_summary_level:'普通观察',bucket:'优先核查',direction:'卖 IM2610 / 买 IM2612',events:[]}];
 assert.equal(radarRows({objects}),objects);
 assert.deepEqual(radarRows(null),[]);
 assert.doesNotMatch(source,/const (priority|directional|deliveryMonth)=/);
 assert.match(source,/new URLSearchParams\(\{\.\.\.query,family\}\)/);
 assert.match(source,/\[filter,setFilter\]=useState\('priority'\)/);
 assert.match(source,/本次未发现符合当前条件的异常/);
});
test('radar keeps small result lists visible and carries candidate evidence into pair research',()=>{
 assert.match(source,/items.length<=10\?' short-results'/);
 assert.match(source,/direction_code:r.direction_code/);
 assert.match(source,/e.far\?'跨期分析'/);
});
test('research card retains saved assumptions, exposes unknown direction and costs',()=>{
 const props={data:{product:'IM',near:'IM2610',far:'IM2612',price_type:'close',meta:{snapshot_id:'test'},spread:{summary:{latest:{trade_date:'2026-09-11',spread:55}}}},onSaved(){}};
 const html=renderToStaticMarkup(React.createElement(module.exports.ResearchCard,props));
 assert.match(html,/待核查，不预设/);assert.match(html,/保存核查卡到自选/);
 assert.match(html,/未知费用留空/);assert.match(html,/记录参考：55/);
 const saved={plan:{direction:'sell',rationale:'测试已有原因',invalidation:'失效',lots:2,reference_spread:40,reference_date:'2026-09-01'}};
 const restored=renderToStaticMarkup(React.createElement(module.exports.ResearchCard,{...props,saved}));
 assert.match(restored,/测试已有原因/);assert.match(restored,/记录参考：40/);
});
test('basis panel distinguishes index close and CU reference scopes before data loads',()=>{
 const html=renderToStaticMarkup(React.createElement(BasisPanel,{product:'CU',priceType:'close',onProduct(){},Chart(){return null;}}));
 assert.match(html,/不是交割基差/);assert.match(html,/其他品种不以近似指标替代/);
 assert.match(html,/IM \/ IF \/ IC \/ IH：指数日收盘基差/);
 assert.match(source,/正值：期货贴水；负值：期货升水/);
 assert.match(source,/重选当前活跃合约/);
});
test('contract rules remain scoped references and do not overwrite saved research assumptions',()=>{
 const terms={multiplier:200,tick_size:0.2,tick_value:40,settlement:'现金交割',source_url:'https://www.cffex.com.cn/zz1000/',reviewed_on:'2026-09-12',calendar_source_url:'https://www.cffex.com.cn/',expiry_rule:'第三个周五',as_of:'2026-09-11',contracts:[{symbol:'IM2609',expected_last_trade_date:'2026-09-18',calendar_days_remaining:7},{symbol:'IM2703',expected_last_trade_date:null,calendar_days_remaining:null}],warnings:['不用于年化']};
 const html=renderToStaticMarkup(React.createElement(module.exports.ContractTerms,{terms}));
 assert.match(html,/单腿每手一跳 40 元/);assert.match(html,/尚余 7 自然日/);
 assert.match(html,/非逐合约最终公告/);assert.match(html,/该年交易日历未核验/);
 assert.equal(renderToStaticMarkup(React.createElement(module.exports.ContractTerms,{})),'');
 const card=renderToStaticMarkup(React.createElement(module.exports.ResearchCard,{data:{contract_terms:terms,product:'IM',near:'IM2609',far:'IM2703',price_type:'close',meta:{snapshot_id:'fixture'},spread:{summary:{latest:null}}},saved:{plan:{lots:1,multiplier:10,total_cost:null,rationale:'原假设',invalidation:''}},onSaved(){}}));
 assert.match(card,/采用已核验乘数 200/);assert.match(card,/当前假设与中金所乘数不同/);
 assert.match(card,/手工假设/);assert.match(card,/value="10"/);
 assert.match(source,/key==='multiplier'\?\{multiplier_source:null\}/);
});
test('radar starts with all-product controls and does not require choosing a product',()=>{
 const html=renderToStaticMarkup(React.createElement(RadarPanel,{onSelect(){}}));
 assert.match(html,/全品种初筛/);
 assert.match(html,/扫描全部品种/);
 assert.match(html,/name="min_volume"/);
 assert.match(html,/name="min_position"/);
 assert.doesNotMatch(html,/name="product"/);
 assert.match(html,/基本面不参与排名/);
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
