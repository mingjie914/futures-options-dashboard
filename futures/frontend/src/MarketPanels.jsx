import {useEffect,useRef,useState} from 'react';
const fmt=v=>v==null?'—':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:2});
const weak=(p,k)=>p?.['near_'+k]==null||p?.['far_'+k]==null?null:Math.min(p['near_'+k],p['far_'+k]);
export const passesScan=(o,minHistory=0,minVolume=0)=>(minHistory<=0||(o.latest?.history_n??-1)>=minHistory)&&(minVolume<=0||(weak(o.latest,'volume')??-1)>=minVolume);
// Object grouping, family filtering and direction are owned by the API.
export const radarRows=data=>data?.objects||[];

export function FundamentalCandidates({revision,Chart}) {
 const [catalog,setCatalog]=useState(null),[code,setCode]=useState(''),[series,setSeries]=useState(null),[error,setError]=useState(''),[kind,setKind]=useState('all');
 useEffect(()=>{const c=new AbortController();setCatalog(null);setError('');fetch('/api/fundamentals/candidates',{signal:c.signal}).then(async r=>{const d=await r.json();if(!r.ok)throw Error(d.detail);return d;}).then(setCatalog).catch(e=>{if(e.name!=='AbortError')setError(e.message);});return()=>c.abort();},[revision]);
 useEffect(()=>{const c=new AbortController();setSeries(null);if(!code||!catalog)return()=>c.abort();setError('');
 (async()=>{let offset=0,points=[];do{const r=await fetch('/api/fundamentals/candidates?'+new URLSearchParams({code,offset,limit:5000}),{signal:c.signal});const d=await r.json();if(!r.ok)throw Error(d.detail);if(d.snapshot_sha256!==catalog.snapshot_sha256||d.terminal_metadata?.metadata_sha256!==catalog.terminal_metadata?.metadata_sha256)throw Error('候选版本已变化，请刷新数据。');points.push(...d.points);if(d.next_offset!==null&&d.next_offset<=offset)throw Error('候选分页未前进。');offset=d.next_offset;}while(offset!==null);if(!c.signal.aborted)setSeries({code,points});})().catch(e=>{if(e.name!=='AbortError')setError(e.message);});return()=>c.abort();},[catalog,code]);
 const item=catalog?.indicators.find(r=>r.code===code),points=series?.code===code?series.points:[],terminal=item?.terminal_metadata;
 const labels={reference_candidate:'研究参考候选',futures_excluded:'期货序列 · 非独立现货',background:'其他背景 · 不作对应商品现货',unknown:'身份待核验'};
 const choices=(catalog?.indicators||[]).filter(r=>kind==='all'||(r.terminal_metadata?.classification||'unknown')===kind);
 return <section className="panel"><h2>候选原始序列 · 同码属性核验</h2>
 <p>用于逐码核对已保存数据，不进入基差、雷达或回测。当前终端属性与旧原始观测分开保存；未提供属性证据时，名称、单位与正式频率仍未知。</p>
 {error&&<p role="alert">{error}</p>}{!catalog&&!error?<p role="status">读取候选目录…</p>:catalog&&<details><summary>{catalog.indicators.length} 个候选指标 · {fmt(catalog.observations)} 个保存观测 · 展开核验</summary>
 {catalog.terminal_metadata&&<p role="status">当前终端属性已核验 {catalog.terminal_metadata.unique_codes} 码；{Object.entries(catalog.terminal_metadata.counts).map(([key,n])=>(labels[key]||key)+' '+n).join(' · ')}。不是新增观测或交易准入数量。</p>}
 <div className="scan-controls"><label>候选用途<select value={kind} onChange={e=>{setKind(e.target.value);setCode('');}}><option value="all">全部保存候选</option>{Object.entries(labels).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label>
 <label>候选编码<select value={code} onChange={e=>setCode(e.target.value)}><option value="">选择编码查看原始序列</option>{choices.map(r=><option key={r.code} value={r.code}>{r.code} · {r.terminal_metadata?.name||'旧声明：'+r.claimed_labels.join(' / ')}</option>)}</select></label></div>
 {item&&<><p>{item.code} · 已保存观测 {item.first} — {item.last} · {fmt(item.observations)} 个观测</p>
 {terminal&&<><h3>{terminal.name}</h3><p>{labels[terminal.classification]} · 当前终端默认：{{D:'日',W:'周',M:'月',Y:'年'}[terminal.frequency]||terminal.frequency} / {terminal.unit} · {terminal.source}{terminal.source_country?' · '+terminal.source_country:''}</p><p className="sub">终端显示范围 {terminal.display_start} — {terminal.display_end}；显示更新时间 {terminal.display_updated_on}。不是本地取数水位，也不是全部历史观测的首次发布时间。</p><ul>{terminal.limits.map((text,i)=><li key={i}>{text}</li>)}</ul></>}
 <details><summary>旧声明及截图证据</summary><p>旧线索：{item.claimed_labels.join('；')}。固定编号：{item.references.join('、')}。</p>
 {item.source_metadata_claims?.length>1&&<p role="alert">本地同码存在 {item.source_metadata_claims.length} 种元数据声明：{item.source_metadata_claims.map(r=>r.name).join('；')}。未自动选择或统一口径。</p>}
 {terminal&&<p className="sub">同码截图 SHA256：{terminal.evidence.source_sha256.join('；')}。旧声明保留，不覆盖原始值。</p>}</details>
 {!series&&!error?<p role="status">读取候选原始值…</p>:points.length>0&&<><Chart rows={points} x="observation_date" lines={[[ 'raw_value',code+' 原始值（单位未核验）' ]]}/><p className="sub">观测顺序等距展示；连线不代表期间每天有值，不补零、不前填、不换算。页面显示数值按两位小数展示，原始精度保留在数据中。</p><a href={'/api/fundamentals/candidates?code='+code+'&limit=5000'} target="_blank" rel="noreferrer">查看原始值、逐点来源哈希与分页信息</a></>}</>}
 <ul>{catalog.warnings.map(w=><li key={w}>{w}</li>)}</ul></details>}
 </section>;
}

export function FundamentalUpdateStatus({record,error}) {
 if(error)return <div role="alert">基本面更新记录读取失败：{error}。不影响旧快照读取，不触发取数。</div>;
 if(!record)return <p role="status">读取基本面独立更新记录…</p>;
 const count=value=>value==null?'未知':fmt(value);
 return <div className="update-status">
 <h3>Wind EDB · 独立更新状态</h3>
 <p role="status">{record.label}</p>
 <dl><div><dt>最近本地检查</dt><dd>{record.checked_at||'时间未核验'}（不是指标取数或上游发布时间）</dd></div>
 <div><dt>本次请求截止</dt><dd>{record.requested_end||'未知'}（不是最新观测日期）</dd></div>
 <div><dt>本次执行方式</dt><dd>{record.executed===false?'仅计划，未执行':record.executed===true?(record.network_fetch_enabled===false?'保存结果重放，未启用取数':record.network_fetch_enabled===true?'允许取数，不代表实际调用或获得新数据':'已执行，取数开关未知'):'执行情况未知'}</dd></div>
 <div><dt>独立执行检查点</dt><dd>{record.checkpoint?.label||'暂无记录'} · 截止 {record.checkpoint?.requested_end||'未知'}{record.checkpoint?.lock_present?' · 锁文件存在，不代表进程仍存活':''}</dd></div></dl>
 {(record.issues||[]).map((issue,i)=><p role="alert" key={i}>{issue}</p>)}
 <details><summary>请求计划与检查前水位 · {(record.waterlines||[]).length}个指标</summary>
 <p>历史请求缺口：{count(record.counts?.pending_requests)}；检查前待查发布区间：{count(record.counts?.release_check_requests)}；等待发布记录：{count(record.counts?.awaiting_release)}。</p>
 <p className="sub">区间计数不是缺失交易日数。待查发布区间是执行前计划，不代表执行后仍未完成。低频水位落后请求截止，不自动视为断更。</p>
 {(record.waterlines||[]).length?<div className="table-wrap"><table><thead><tr><th>指标编码</th><th>本次检查前已接受水位</th></tr></thead><tbody>{record.waterlines.map(row=><tr key={row.code}><td>{row.code}</td><td>{row.date||'未知'}</td></tr>)}</tbody></table></div>:<p>没有可核验的检查前水位。</p>}
 </details>
 <p className="sub">这里只读EDB隔离运行记录，不判断记录中的进程当前是否存活。检查完成不等于数据齐全或生产发布；当前快照观测日期见下方指标。CEIC仍走授权导出审阅，本页不启动取数、RDS写入或调度。</p>
 </div>;
}

export function ContractTerms({terms}) {
 if(!terms)return null;
 return <details><summary>合约规则 · 乘数 {fmt(terms.multiplier)} 元/点/手 · 最小跳动 {fmt(terms.tick_size)} 点</summary>
 <p>单腿每手一跳 {fmt(terms.tick_value)} 元 · {terms.settlement}。<a href={terms.source_url} target="_blank" rel="noreferrer">中金所合约表</a> · 核验 {terms.reviewed_on}</p>
 <p>{terms.expiry_rule}。<a href={terms.calendar_source_url} target="_blank" rel="noreferrer">2026休市安排</a>；以下为推算，非逐合约最终公告。</p>
 <ul>{terms.contracts.map(c=><li key={c.symbol}>{c.symbol}：预计 {c.expected_last_trade_date||'未知（该年交易日历未核验）'}{c.calendar_days_remaining!=null?` · 相对源截止 ${terms.as_of} ${c.calendar_days_remaining<0?'已过':'尚余'} ${Math.abs(c.calendar_days_remaining)} 自然日`:''}</li>)}</ul>
 <ul>{terms.warnings.map(w=><li key={w}>{w}</li>)}</ul></details>;
}

export function ResearchCard({data,saved,candidate,onSaved}) {
 const latest=data.spread.summary.latest;
 const terms=data.contract_terms;
 const initial=()=>saved?.plan||{direction:candidate?.direction_code||null,
  rationale:candidate?.rationale?'雷达均值回归候选：'+candidate.rationale:'',
  reference_spread:latest?.spread??null,reference_date:latest?.trade_date||null,snapshot_id:data.meta.snapshot_id,
  entry_spread:null,exit_spread:null,invalidation:'',lots:1,multiplier:null,total_cost:null,review_date:null};
 const [plan,setPlan]=useState(initial),[scenario,setScenario]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[message,setMessage]=useState('');
 const calculationVersion=useRef(0);
 useEffect(()=>{calculationVersion.current++;setPlan(initial());setScenario(null);setMessage('');return()=>{calculationVersion.current++;};},[saved?.id,saved?.plan_updated_at]);
 const change=(key,value)=>{calculationVersion.current++;setPlan(p=>({...p,[key]:value,...(key==='multiplier'?{multiplier_source:null}:{})}));setScenario(null);setMessage('');};
 async function request(url,options){const r=await fetch(url,options),d=await r.json();if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:JSON.stringify(d.detail));return d;}
 const send=body=>({headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 async function save(e){e.preventDefault();setBusy(true);setError('');try{
  let item=saved;
  if(!item){const result=await request('/api/watchlists',{method:'POST',...send({product:data.product,near:data.near,far:data.far,price_type:data.price_type})});item=result.items.find(i=>i.product===data.product&&i.near===data.near&&i.far===data.far&&i.price_type===data.price_type);}
  const result=await request('/api/watchlists/'+item.id,{method:'PATCH',...send(plan)});onSaved(result.items);setMessage('已保存到本地自选；不是下单指令');
 }catch(e){setError(e.message);}finally{setBusy(false);}}
 async function calculate(){const version=++calculationVersion.current;setBusy(true);setError('');setScenario(null);try{const keys=['direction','entry_spread','exit_spread','lots','multiplier','total_cost'];const result=await request('/api/scenario',{method:'POST',...send(Object.fromEntries(keys.map(k=>[k,plan[k]])))});if(version===calculationVersion.current)setScenario(result);}catch(e){if(version===calculationVersion.current)setError(e.message);}finally{setBusy(false);}}
 return <section className="panel research-card"><h2>交易核查卡 · 成本情景与本地自选</h2>
 <p className="sub">{data.near} − {data.far} · {data.price_type==='close'?'收盘':'结算'}。方向为研究假设；同乘数、等手数，未知费用留空。离开页面前请保存。</p>
 <ContractTerms terms={terms}/>
 {terms&&<p><button type="button" onClick={()=>{calculationVersion.current++;setPlan(p=>({...p,multiplier:terms.multiplier,multiplier_source:terms.version+' '+terms.source_url}));setScenario(null);setMessage('已采用已核验乘数；费用仍需手填并保存');}}>采用已核验乘数 {fmt(terms.multiplier)}</button>{plan.multiplier!=null&&plan.multiplier!==terms.multiplier&&<span role="status"> 当前假设与中金所乘数不同，请核查。</span>}</p>}
 {plan.multiplier!=null&&<p className="sub">乘数依据：{plan.multiplier_source||'手工假设，未记录核验来源'}</p>}
 <p>记录参考：{fmt(plan.reference_spread)} · {plan.reference_date||'无观测'} <button type="button" disabled={!latest} onClick={()=>{calculationVersion.current++;setPlan(p=>({...p,reference_spread:latest.spread,reference_date:latest.trade_date,snapshot_id:data.meta.snapshot_id}));setScenario(null);setMessage('');}}>采用当前观测作为参考</button></p>
 <form onSubmit={save}>
 <label>研究方向<select value={plan.direction||''} onChange={e=>change('direction',e.target.value||null)}><option value="">待核查，不预设</option><option value="buy">买近卖远</option><option value="sell">卖近买远</option></select></label>
 {[['entry_spread','计划入场价差'],['exit_spread','目标价差'],['lots','每腿手数'],['multiplier','合约乘数'],['total_cost','开平仓全部成本 / 元']].map(([key,label])=><label key={key}>{label}<input type="number" step={key==='lots'?1:'any'} min={key==='lots'?1:key==='multiplier'?0.000001:key==='total_cost'?0:undefined} required={key==='lots'} value={plan[key]??''} onChange={e=>change(key,e.target.value===''?null:Number(e.target.value))}/></label>)}
 <label>下次复核日期<input type="date" value={plan.review_date||''} onChange={e=>change('review_date',e.target.value||null)}/></label>
 <label className="wide-field">关注原因<textarea maxLength={2000} rows={2} value={plan.rationale} onChange={e=>change('rationale',e.target.value)}/></label>
 <label className="wide-field">失效条件 / 风险核查<textarea maxLength={2000} rows={2} value={plan.invalidation} placeholder="例如价差、流动性或基本面条件变化后重新评估" onChange={e=>change('invalidation',e.target.value)}/></label>
 <button type="button" disabled={busy||!plan.direction||plan.entry_spread==null||plan.exit_spread==null||!plan.lots} onClick={calculate}>计算成本情景</button><button className="primary" disabled={busy}>保存核查卡到自选</button>
 </form>{error&&<p role="alert">{error}</p>}{message&&<p role="status">{message}</p>}
 {saved?.plan_updated_at&&<p className="sub">上次保存 {saved.plan_updated_at}</p>}
 {scenario&&<p role="status">毛损益 {fmt(scenario.gross_pnl)} 元 / 净损益 {fmt(scenario.net_pnl)} 元 / 盈亏平衡价差 {fmt(scenario.breakeven_exit_spread)}{scenario.net_pnl==null?' · 乘数或成本未知，不能确认净收益':''}。仅情景计算，非账户损益。</p>}
 <details><summary>研究参考版本与边界</summary><p>参考快照 {plan.snapshot_id||'未知'}。不自动采用现价为计划入场价，不自动补齐手续费、保证金、分红或交割条件；不启用定时通知或自动交易。</p></details>
 </section>;
}

export function BasisPanel({product,start,end,priceType,revision,Chart,onProduct}) {
 const [symbol,setSymbol]=useState(''),[data,setData]=useState(null),[error,setError]=useState('');
 useEffect(()=>{const c=new AbortController();setData(null);setError('');const q=new URLSearchParams({product,price_type:priceType});for(const [k,v] of Object.entries({start,end,symbol}))if(v)q.set(k,v);
 fetch('/api/basis-reference?'+q,{signal:c.signal}).then(async r=>{const d=await r.json();if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:JSON.stringify(d.detail));return d;}).then(d=>{if(!c.signal.aborted)setData(d);}).catch(e=>{if(e.name!=='AbortError')setError(e.message);});return()=>c.abort();},[product,start,end,symbol,priceType,revision]);
 const last=data?.latest,isIndex=data?.status==='index_close_basis',unit=isIndex?'指数点':'元每吨',label=isIndex?'日收盘基差':'周频参考价差';
 return <section className="panel"><h2>基差分析 · 分品种开放</h2><p className="sub">IM / IF / IC / IH：指数日收盘基差；CU：周频参考价差，不是交割基差。两类均非实时可执行套利收益，其他品种不以近似指标替代。</p>
 {error?<><p role="alert">{error}</p>{symbol&&<button onClick={()=>setSymbol('')}>重选当前活跃合约</button>}</>:!data?<p role="status">读取已保存的参考价与期货数据…</p>:data.status==='unavailable'?<><h3>{product} 当前条件不可用</h3><p>{data.reason}</p><button onClick={()=>onProduct('CU')}>查看CU周频参考价差</button><p className="sub">无需等待所有品种齐备才开放；但对应品种的数据未确认时不计算。此页面不会触发取数。</p></>:<>
 <label>期货合约<select value={symbol||data.symbol} onChange={e=>setSymbol(e.target.value)}>{data.contracts.map(s=><option key={s}>{s}</option>)}</select></label>
 <p>{data.reference.name} · {data.reference.code} · {isIndex?'日':'周'} / {data.reference.unit}</p><p className="sub">期货源截止 {data.meta.as_of}{data.meta.stale?' · 缓存降级':''} · {isIndex?'指数':'参考价'}序列 {data.reference.first} — {data.reference.last}。以下只报同日配对，不拼接其他合约。</p>
 <ContractTerms terms={data.contract_terms}/>
 <div className="metrics"><article><p>最近{label} / {unit}</p><strong>{fmt(last?.reference_spread)}</strong><small>{last?'配对观测日 '+last.trade_date:'无有效同日配对'}{isIndex&&<><br/>正值：期货贴水；负值：期货升水</>}</small></article><article><p>配对日{isIndex?'指数收盘':'参考价'} / {unit}</p><strong>{fmt(last?.reference_price)}</strong><small>非实时可成交报价</small></article><article><p>配对日期货价 / {unit}</p><strong>{fmt(last?.futures_price)}</strong><small>{data.symbol} · {priceType==='close'?'收盘':'结算'}</small></article><article><p>有效 / 未配对观测</p><strong>{data.matched} / {data.unmatched}</strong><small>不前填，不补零</small></article></div>
 {last&&<p className="notice">最新配对观测距期货源截止 {Math.max(0,Math.round((Date.parse(data.meta.as_of)-Date.parse(last.trade_date))/86400000))} 个日历日。{isIndex?`基差率 ${fmt(last.basis_ratio*100)}%（非年化）；尚未扣除分红、资金和交易成本。`:'不是当前可成交基差；历史首次发布时间未知。'}</p>}
 {data.series.length?<><h3>{isIndex?'指数收盘 S − 期货收盘 F':'参考价 − 同观测日期期货价'}</h3><Chart rows={data.series} lines={[["reference_spread",label+' / '+unit]]}/>{isIndex&&<details><summary>基差率 / (S−F) ÷ S，非年化</summary><Chart rows={data.series} lines={[["basis_ratio","基差率"]]} percent/></details>}<h3>配对价格来源</h3><Chart rows={data.series} lines={[["reference_price",isIndex?data.reference.name+' 收盘':'参考价'],["futures_price",data.symbol]]}/></>:<p>当前日期窗口无有效观测。</p>}
 <ul>{[...data.warnings,...data.reference.limits].map(w=><li key={w}>{w}</li>)}</ul><details><summary>逐点核对与数据版本</summary><p>现货快照 {data.reference_snapshot} · 期货快照 {data.meta.snapshot_id}</p><div className="table-wrap"><table><thead><tr><th>观测日</th><th className="num">{isIndex?'指数收盘':'参考价'}</th><th className="num">期货价</th><th className="num">{label}</th>{isIndex&&<th className="num">基差率</th>}</tr></thead><tbody>{data.series.map(p=><tr key={p.trade_date}><td>{p.trade_date}</td><td className="num">{fmt(p.reference_price)}</td><td className="num">{fmt(p.futures_price)}</td><td className="num">{fmt(p.reference_spread)}</td>{isIndex&&<td className="num">{p.basis_ratio==null?'—':fmt(p.basis_ratio*100)+'%'}</td>}</tr>)}</tbody></table></div></details>
 </>}
 </section>;
}

export function RadarPanel({onSelect}) {
 const [family,setFamily]=useState('all');
 const [query,setQuery]=useState({price_type:'close',min_volume:1000,min_position:1000}),[revision,setRevision]=useState(0),[data,setData]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[filter,setFilter]=useState('priority'),[search,setSearch]=useState('');
 useEffect(()=>{const c=new AbortController();setBusy(true);setError('');setData(null);
  fetch('/api/radar?'+new URLSearchParams({...query,family}),{signal:c.signal}).then(async r=>{const d=await r.json();if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:JSON.stringify(d.detail));return d;}).then(d=>{if(!c.signal.aborted)setData(d);}).catch(e=>{if(e.name!=='AbortError')setError(e.message);}).finally(()=>{if(!c.signal.aborted)setBusy(false);});return()=>c.abort();
 },[query,revision,family]);
 const items=radarRows(data).filter(r=>r.product.includes(search.trim().toUpperCase())&&(filter==='all'||filter==='signals'&&r.events.length>0&&r.bucket!=='数据待检查'||filter==='priority'&&r.bucket==='优先核查'||filter==='watch'&&r.bucket==='观察名单'||filter==='quality'&&(r.quality.length>0||r.bucket==='数据待检查')));
 return <><section className="panel"><h2>全品种初筛 · 日终量价</h2><p className="sub">先找值得检查的品种，再验证合约。异常不是交易指令；基本面不参与排名。</p>
 <form onSubmit={e=>{e.preventDefault();const v=Object.fromEntries(new FormData(e.currentTarget));if(!v.end)delete v.end;setQuery(v);setRevision(revision+1);}}>
 <label>价格口径<select name="price_type" defaultValue="close"><option value="close">收盘价</option><option value="settle">结算价</option></select></label><label>截止日期<input type="date" name="end"/></label><label>每腿最低成交 / 手<input type="number" name="min_volume" min="0" max="10000000" step="1" defaultValue="1000" required/></label><label>每腿最低持仓 / 手<input type="number" name="min_position" min="0" max="10000000" step="1" defaultValue="1000" required/></label><button disabled={busy}>{busy?'正在扫描…':'扫描全部品种'}</button></form>
 <p className="sub">修改门槛后点击扫描生效；重复扫描和类型切换复用未变化的本地快照。</p>
 {busy&&<p role="status">正在扫描本地窗口，首次可能需要数秒；不启动上游取数。</p>}{error&&<p role="alert">扫描失败：{error}。未将失败当作“没有异常”。</p>}
 </section>{data&&<><div className="metrics">{[['已扫描品种',`${data.scanned} / ${data.expected}`],['优先核查对象',data.object_counts.priority],['异常合约对',data.object_counts.abnormal_pairs],['读取失败',data.failures.length]].map(([label,value])=><article key={label}><p>{label}</p><strong>{value}</strong></article>)}</div>
 <section className="panel radar-panel"><p className="sub">市场最新观测 {data.as_of||'无'} · 截止 {data.requested_end} · {data.price_type==='close'?'收盘价':'结算价'} · {data.cached?'复用扫描':'本次扫描'} · 原计算 {fmt(data.seconds)} 秒。顶部计数为全部类型。</p>
 <div className="scan-controls"><label>异常类型<select value={family} onChange={e=>setFamily(e.target.value)}><option value="all">全部类型</option><option value="position">仅持仓异常</option><option value="spread">仅价差异常</option><option value="volume">仅成交异常</option></select></label><label>显示<select value={filter} onChange={e=>setFilter(e.target.value)}><option value="priority">优先核查</option><option value="watch">观察名单</option><option value="signals">全部异常</option><option value="quality">数据待检查</option><option value="all">全部已扫描</option></select></label><label>品种代码<input value={search} onChange={e=>setSearch(e.target.value)} placeholder="如 IM、CU"/></label><span>{items.length} 个对象 / {new Set(items.map(r=>r.product)).size} 个品种</span></div>
 <p className="notice">价差＝近月－远月；方向仅为均值回归候选。尚未做季节、到期和成本调整；下钻可核对同月份历史。</p>
 <div className={'table-wrap'+(items.length<=10?' short-results':'')}><table><thead><tr><th>品种 / 合约对象</th><th>分组</th><th>方向参考</th><th>触发依据</th><th>限制与下钻</th></tr></thead><tbody>{items.map(r=><tr key={r.key}><td>{r.product}<small>{r.near?r.near+(r.far?' / '+r.far:''):'品种合计'}</small><small>{r.as_of}</small></td><td>{r.bucket}</td><td>{r.direction}{r.direction!=='暂无方向'&&<small>均值回归候选</small>}</td><td>{r.events.length?r.events.map((e,i)=><div className="radar-evidence" key={i}>{e.label}：{fmt(e.value)}<small>{e.percentile!=null?'历史分位 '+fmt(e.percentile)+'% · ':''}基准样本 {e.n}{e.short?' · 短样本':''}</small></div>):'未命中规则；不代表无机会'}</td><td>{r.limits.map((v,i)=><small key={i}>{v}</small>)}{r.events.length?<button className="link" onClick={()=>{const e=r.events.find(e=>e.family==='spread')||r.events[0];onSelect(r.product,{...e,direction_code:r.direction_code,rationale:r.events.map(e=>e.label).join('；')},data,e.far?'跨期分析':e.family==='position'?'持仓换月':'机会扫描');}}>查看{r.far?'合约对':r.near?'合约':'品种'} →</button>:<button className="link" onClick={()=>onSelect(r.product,null,data,'机会扫描')}>查看品种 →</button>}<details><summary>品种检查范围</summary><small>共检查 {r.examined_pairs} 对，量仓门槛排除 {r.skipped_pairs} 对；品种汇总：{r.product_summary_level}</small></details></td></tr>)}</tbody></table></div>
 {!items.length&&<p className="empty">本次未发现符合当前条件的异常。可查看“观察名单”“全部异常”或读取失败清单；不会用普通结果填充。</p>}
 {data.failures.length>0&&<div role="alert"><h3>读取失败（不计为无异常）</h3><ul>{data.failures.map(f=><li key={f.product}>{f.product}：{f.reason}</li>)}</ul></div>}
 <details><summary>扫描规则、范围与限制</summary><p>优先核查：至少60个基准样本，价差分位≤1%/≥99%或同一对象至少两类成熟异常，且无已报告质量问题。类型筛选由接口按所选证据重新分组；不合并不同对象升级。</p><ul>{data.warnings.map(w=><li key={w}>{w}</li>)}</ul><p>价差位置 ≤5% / ≥95%；1、5观测日变动绝对值分位 ≥97.5%；成交放大 ≥2倍。合约对为真实月份相邻与流动性合格持仓前三合约之间的去重组合。</p><p>窗口 {data.start} — {data.requested_end} · 快照 {data.snapshot_id} · {data.version} · 生成 {data.generated_at}</p></details></section></>}</>;
}

export function Trend({points=[]}) {
 const values=points.map(p=>p.spread).filter(Number.isFinite);
 if(!values.length)return <span>无走势样本</span>;
 const min=Math.min(...values),max=Math.max(...values),segments=[];let segment=[];
 points.forEach((p,i)=>{if(!Number.isFinite(p.spread)){if(segment.length)segments.push(segment);segment=[];}else segment.push(`${3+i*114/Math.max(1,points.length-1)},${max===min?20:36-(p.spread-min)/(max-min)*32}`);});
 if(segment.length)segments.push(segment);
 return <svg width="120" height="40" viewBox="0 0 120 40" role="img" aria-label={`近期价差趋势，${values.length}个有效配对`}><title>{`${points[0]?.trade_date} 至 ${points.at(-1)?.trade_date}；独立纵轴，仅比较走势`}</title>{segments.map((s,i)=>s.length===1?<circle key={i} cx={s[0].split(',')[0]} cy={s[0].split(',')[1]} r="2" fill="#2563eb"/>:<polyline key={i} points={s.join(' ')} fill="none" stroke="#2563eb" strokeWidth="1.7"/>)}</svg>;
}

export function ScanPanel({data,onPair}) {
 const [sort,setSort]=useState('month'),[only,setOnly]=useState(false);
 const [minHistory,setMinHistory]=useState(0),[minVolume,setMinVolume]=useState(0);
 const rows=data.opportunities.filter(o=>passesScan(o,minHistory,minVolume)&&(!only||(o.latest?.historical_percentile!=null&&(o.latest.historical_percentile<=10||o.latest.historical_percentile>=90))));
 const score=o=>sort==='volume'?weak(o.latest,'volume'):sort==='position'?weak(o.latest,'position'):o.latest?.historical_percentile==null?null:Math.abs(o.latest.historical_percentile-50);
 if(sort!=='month')rows.sort((a,b)=>(score(b)??-Infinity)-(score(a)??-Infinity));
 return <section className="panel scan-panel"><h2>相邻月份 · 候选组合扫描</h2><p className="sub">当前品种已观测合约；先筛选，再点击组合进入分析。极端分位仅表示待检查，不代表买卖信号。</p>
 <div className="scan-controls"><label>排序<select value={sort} onChange={e=>setSort(e.target.value)}><option value="month">合约月份</option><option value="extreme">分位偏离中位数</option><option value="volume">较弱腿成交量</option><option value="position">较弱腿持仓量</option></select></label><label className="check"><input type="checkbox" checked={only} onChange={e=>setOnly(e.target.checked)}/>仅看分位 ≤10% 或 ≥90%</label><label>历史样本至少<input type="number" min="0" step="1" value={minHistory} onChange={e=>setMinHistory(Math.max(0,Number(e.target.value)))}/></label><label>较弱腿成交至少 / 手<input type="number" min="0" step="1" value={minVolume} onChange={e=>setMinVolume(Math.max(0,Number(e.target.value)))}/></label><span>{rows.length} / {data.opportunities.length} 个组合</span></div>
 <p className="sub">0 表示不限制；筛选阈值由使用者设定，不保证可成交。少于60个历史样本仅作短样本观察，60不是统计显著性标准。表格可横向滚动查看量仓与数据检查；无结果时可降低阈值。</p>
 <div className="table-wrap"><table><thead><tr>{['组合 / 配对日期','价差','较前次变化','近期走势 / 最多60个观测','历史分位','分位样本 / 配对总数','较弱腿成交 / 手','较弱腿持仓 / 手','数据检查'].map((h,i)=><th className={[1,2,4,5,6,7].includes(i)?'num':undefined} key={h}>{h}</th>)}</tr></thead><tbody>{rows.map(o=>{const p=o.latest;const flags=[!p?'无配对数据':p.trade_date!==data.meta.as_of?'配对日期落后':null,p?.historical_percentile==null?'分位不足':null,o.missing_dates.length?'存在未配对日期':null,weak(p,'volume')==null||weak(p,'position')==null?'量仓缺失':null,data.meta.stale?'缓存降级':null].filter(Boolean);return <tr key={o.near+o.far}><td><button className="link" onClick={()=>onPair(o.near,o.far)}>{o.near} / {o.far}</button><small>{p?.trade_date||'—'}</small></td><td className="num">{fmt(p?.spread)}</td><td className="num">{fmt(p?.change)}</td><td><Trend points={o.trend}/><button className="link" aria-label={`查看${o.near}减${o.far}走势`} onClick={()=>onPair(o.near,o.far)}>查看走势 →</button></td><td className="num">{p?.historical_percentile==null?'—':fmt(p.historical_percentile)+'%'}</td><td className="num">{fmt(p?.history_n)} / {fmt(o.sample_count)}</td><td className="num">{fmt(weak(p,'volume'))}</td><td className="num">{fmt(weak(p,'position'))}</td><td>{flags.join(' · ')||'配对同日 · 分位可用'}</td></tr>;})}</tbody></table></div>{!rows.length&&<p className="empty">当前筛选无组合；可取消分位筛选。</p>}<p className="sub">较弱腿取两腿较小值，任一缺失则不报值；量仓对应配对日期，不是实时盘口。未配对日期可能来自上市时间不同或数据缺失，不能直接判断缺数；变化相对前次有效配对观测，可能跨日。分位使用此前最多252个观测，不含当日。</p></section>;
}

export function CurvePanel({data,onPair,Chart}) {
 const [near,setNear]=useState(''),[far,setFar]=useState('');
 const curve=data.curve, comparisons=curve.comparisons||[];
 const rows=curve.nodes.map((n,i)=>({...n,...Object.fromEntries(comparisons.map(c=>['lag'+c.lag,c.nodes[i]?.price??null]))}));
 return <><section className="panel"><h2>期限曲线 · 同合约跨日期对照</h2><p className="sub">横轴为真实合约月份，不是精确到期距离；历史曲线按当日合约对齐，缺失不替代、不填值。</p><Chart rows={rows} x="symbol" lines={[["price",'当日 '+data.meta.as_of],...comparisons.filter(c=>c.as_of).map(c=>['lag'+c.lag,`前${c.lag}个观测日 ${c.as_of}`])]}/><p className="sub">{comparisons.map(c=>`前${c.lag}日：${c.as_of||'窗口内观测不足'}`).join(' · ')}。观测日来自本品种日线，不等同于已核验交易日历；整品种缺数无法识别。不计算到期年化收益。</p></section>
 <section className="panel"><h2>相邻价差 · 选择分析组合</h2><p className="sub">近月减远月 / 原始报价单位。点击组合保留当前日期与价格口径。</p><div className="curve-pairs">{curve.adjacent.map(p=><button key={p.near+p.far} onClick={()=>onPair(p.near,p.far)}>{p.near} − {p.far}<strong>{fmt(p.spread)}</strong></button>)}</div><div className="scan-controls"><label>选取近月<select value={near} onChange={e=>setNear(e.target.value)}><option value="">请选择</option>{curve.nodes.map(n=><option key={n.symbol}>{n.symbol}</option>)}</select></label><label>选取远月<select value={far} onChange={e=>setFar(e.target.value)}><option value="">请选择</option>{curve.nodes.map(n=><option key={n.symbol}>{n.symbol}</option>)}</select></label><button disabled={!near||!far||near>=far} onClick={()=>onPair(near,far)}>分析所选两腿</button></div></section>
 <section className="panel"><h2>各合约变化与量仓分布</h2><p className="sub">价格变化按同一真实合约计算；成交与持仓均为当日观测手数，分图展示。</p><div className="grid"><Chart rows={rows} x="symbol" lines={[["volume","成交量 / 手"]]} integer bar/><Chart rows={rows} x="symbol" lines={[["position","持仓量 / 手"]]} integer bar/></div><div className="table-wrap"><table><thead><tr><th>合约</th><th className="num">当日价格</th>{comparisons.map(c=><th className="num" key={c.lag}>较前{c.lag}观测日变化</th>)}<th className="num">成交 / 手</th><th className="num">持仓 / 手</th></tr></thead><tbody>{curve.nodes.map((n,i)=><tr key={n.symbol}><td>{n.symbol}</td><td className="num">{fmt(n.price)}</td>{comparisons.map(c=><td className="num" key={c.lag}>{fmt(c.nodes[i]?.change)}</td>)}<td className="num">{fmt(n.volume)}</td><td className="num">{fmt(n.position)}</td></tr>)}</tbody></table></div></section></>;
}
