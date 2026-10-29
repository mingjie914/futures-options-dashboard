import { conditionalFetch as fetch } from '../../../frontend/src/transport.js';
import { useChart, ChartFrame, usePreference } from '../../../frontend/src/charts.js';
import {useEffect, useMemo, useRef, useState} from 'react';
import {lineSegments, tooltipRows, seasonalSelection, inventoryHistory} from './lineSegments.mjs';

const quantity = value => value == null ? '—' : Number(value).toLocaleString('zh-CN',{maximumFractionDigits:2});
const historyHeight = 'clamp(380px, 56vh, 600px)';
const inventoryHeight = 'clamp(320px, 44vh, 460px)';
const inventoryLabel = s => (s.id.endsWith('_W')?'仓库':s.id.endsWith('_F')?'厂库':s.kind==='库存'?'交易所':'')+s.kind;
function InventoryTrend({rows, unit, name, frequency}) {
  const ref = useRef();
  useChart(ref, chart => chart.setOption({animation:false,
    legend:{type:'scroll',top:0,left:0,right:0,itemWidth:18,itemHeight:9,itemGap:16,textStyle:{fontSize:12},data:[{name,icon:'path://M0,3 L18,3 L18,5 L0,5 Z',itemStyle:{color:'#4e7fb9'}},{name:'增加',icon:'rect',itemStyle:{color:'#b64d49'}},{name:'减少',icon:'rect',itemStyle:{color:'#2b8167'}}]},grid:{left:85,right:85,top:45,bottom:60},
    tooltip:{trigger:'axis',confine:true,renderMode:'richText',formatter:params=>{const p=rows[params[0]?.dataIndex];return p ? `${p.date}\n${name}：${quantity(p.value)} ${unit}\n较前次公布${p.previous_date?'（'+p.previous_date+'）':''}：${quantity(p.change)} ${unit}` : '';}},
    xAxis:{type:'category',data:rows.map(p=>p.date),boundaryGap:false},
    yAxis:[{type:'value',scale:true,splitLine:{lineStyle:{color:'#eef0f3'}}},{type:'value',scale:true,splitLine:{show:false}}],
    dataZoom:[{type:'inside'},{type:'slider',height:20,bottom:10}],
    series:[{name,type:'line',showSymbol:false,connectNulls:false,z:3,lineStyle:{width:2.4,color:'#4e7fb9'},itemStyle:{color:'#4e7fb9'},areaStyle:{color:'#4e7fb9',opacity:0.035},data:rows.map(p=>p.value)},
      ...[['增加','#b64d49',1],['减少','#2b8167',-1]].map(([label,color,sign])=>({name:label,type:'bar',yAxisIndex:1,barGap:'-100%',itemStyle:{color,opacity:0.7},data:rows.map(p=>p.change!=null&&p.change*sign>0?p.change:null)}))]
  }),[rows,unit,name,frequency]);
  return <ChartFrame><div ref={ref} className="chart" style={{height:inventoryHeight}} role="img" aria-label={name+'走势与增减'}/></ChartFrame>;
}

export function InventoryPanel({product,end,revision,controls}) {
  const [data,setData]=useState(null),[error,setError]=useState('');
  const [indicator,setIndicator]=usePreference('futures.inventory.'+product+'.indicator',''),[years,setYears]=usePreference('futures.inventory.years',5);
  useEffect(()=>{const c=new AbortController();setData(null);setError('');
    fetch('/api/inventory?'+new URLSearchParams({product,...(end?{end}:{})}),{signal:c.signal}).then(async r=>{const d=await r.json();if(!r.ok)throw Error(d.detail||'库存仓单读取失败');return d;}).then(d=>{if(!c.signal.aborted)setData(d);}).catch(e=>{if(e.name!=='AbortError')setError(e.message);});
    return()=>c.abort();
  },[product,end,revision]);
  const series=data?.series.find(s=>s.id===indicator)||data?.series.find(s=>s.kind==='仓单'&&s.id.endsWith('_W'))||data?.series[0];
  const history=useMemo(()=>inventoryHistory(series?.points||[],end,years),[series,end,years]),last=history.rows.at(-1);
  const selection=<><select aria-label="库存仓单指标" value={data?.series.some(s=>s.id===indicator)?indicator:''} disabled={!data?.series.length} onChange={e=>setIndicator(e.target.value)}><option value="">仓单库存</option>{data?.series.map(s=><option key={s.id} value={s.id}>{inventoryLabel(s)}</option>)}</select><select aria-label="历史跨度" value={years} onChange={e=>setYears(Number(e.target.value))}><option value={5}>近5年</option><option value={0}>全部年份</option></select></>;
  return <><div className="filters page-filters">{controls(selection,last?.date||data?.as_of,!data&&!error)}</div>
    {error?<p className="error" role="alert">{error}</p>:!data?<p className="empty" role="status">读取库存仓单…</p>:!history.rows.length?<p className="empty">{product} 暂无所选日期前的库存仓单数据</p>:<>
      <section className="panel history-panel"><div className="filters history-toolbar"><h2 className="section-label">{inventoryLabel(series)}季节性 / {series.unit}</h2></div>
        <HistoryChart result={{...history,mode:'seasonal',metric:'price',unit:series.unit,accessible_label:series.name+'历年季节性'}} showAllYears height={inventoryHeight}/>
      </section>
      <section className="panel"><h2>走势与增减 / {series.unit}</h2><InventoryTrend rows={history.rows} unit={series.unit} name={inventoryLabel(series)} frequency={series.frequency}/></section>
      <p className="sub">{data.preview?'预览样本 · ':''}{series.source} · {series.frequency==='W'?'周频':'日频'} · 最近公布 {last.date} · 已入库观测，未公布不补零；增减为相邻公布值之差</p>
    </>}
  </>;
}

export function HistoryChart({result, heatmap=false, showAllYears=false, height=historyHeight}) {
  const ref = useRef();
  useChart(ref, chart => {
    const share = result.metric === 'position_share' && result.mode !== 'spread';
    const integer = !share && result.metric !== 'price' && result.mode !== 'spread';
    const display = value => value == null ? '—' : Number(value * (share ? 100 : 1)).toLocaleString('zh-CN', {maximumFractionDigits: integer ? 0 : 2}) + (share ? '%' : '');
    const byLine = result.lines.map(line => new Map(line.points.map(point => [point.x, point])));
    const segments = heatmap ? [] : lineSegments(result.axis, result.lines);
    let selected = result.mode === 'seasonal' ? seasonalSelection(result.lines, showAllYears) : {};
    const palette = ['#4e7fb9', '#568f84', '#a48b59', '#8c7b9f', '#b36c73', '#505c6b', '#4e7fb9', '#65a30d', '#c026d3', '#b45309'];
    const currentIndex = result.mode === 'spread' ? result.lines.findIndex(line => line.year === result.meta?.anchor_year) : -1;
    const currentPoint = result.lines[currentIndex]?.points.filter(p => Number.isFinite(p.value)).at(-1);
    const axisIndex = new Map(result.axis.map((x, i) => [x, i]));
    const cells = [];
    if (heatmap) result.lines.forEach((line, y) => line.points.forEach(p => {
      if (p.value != null) cells.push([axisIndex.get(p.x), y, p.value]);
    }));
    const common = {
      animation: false,
      grid: {left: 90, right: 30, top: 45, bottom: heatmap ? 115 : 60},
      xAxis: {type: 'category', data: result.axis, boundaryGap: heatmap, axisLabel: {hideOverlap: true}},
      dataZoom: [{type: 'inside', xAxisIndex: 0}, {type: 'slider', xAxisIndex: 0, bottom: heatmap ? 65 : 10, height: 20}],
    };
    const option = heatmap ? {
      ...common,
      tooltip: {renderMode: 'richText', formatter: p => `${result.axis[p.value[0]]}\n${result.lines[p.value[1]].label}\n${display(p.value[2])} ${result.unit}`},
      yAxis: {type: 'category', data: result.lines.map(l => l.label), axisLabel: {fontSize: 10}},
      visualMap: {min: 0, max: cells.reduce((max, p) => Math.max(max, p[2]), 1), calculable: true, orient: 'horizontal', left: 'center', bottom: 0, inRange: {color: ['#fff', '#e5e9ef', '#4e7fb9', '#181b20']}, formatter: display},
      series: [{type: 'heatmap', data: cells, progressive: 4000, emphasis: {itemStyle: {borderColor: '#a48b59', borderWidth: 1}}}],
    } : {
      ...common,
      color: ['#4e7fb9', '#568f84', '#a48b59', '#8c7b9f', '#b36c73', '#505c6b', '#4e7fb9', '#65a30d', '#c026d3', '#b45309'],
      legend: {type: 'scroll', orient: 'horizontal', left: 0, right: 0, top: 0, height: 24, itemWidth: 18, itemHeight: 9, itemGap: 16, pageIconSize: 10, textStyle: {fontSize: 12}, formatter: name => name === result.lines[currentIndex]?.label ? name+'（当前）' : name, data: result.lines.map(l => l.label), selected,
        scrollDataIndex: result.mode === 'seasonal' && !showAllYears ? Math.max(0,result.lines.length-5) : 0},
      tooltip: {trigger: 'axis', confine: true, renderMode: 'richText', formatter: params => {
        const list = Array.isArray(params) ? params : [params];
        const rows = tooltipRows(params, result.axis, segments, byLine);
        const heading = list[0]?.axisValue ?? result.axis[list[0]?.dataIndex] ?? '';
        const present = new Set(rows.map(r => r.lineIndex));
        const absent = result.mode === 'overview' ? [] : result.lines.filter((line, i) => !present.has(i) && selected[line.label] !== false);
        return [heading, ...rows.map(({name, marker, point}) =>
          `${marker} ${name===point.trade_date?.slice(0,4)?point.trade_date:name+' · '+point.trade_date}: ${display(point.value)}${share ? '' : ' '+(result.unit || '手')}` +
          (result.mode === 'spread' ? `（近 ${display(point.near_price)} / 远 ${display(point.far_price)}）` : '')),
          ...absent.map(line => `${line.label}: —`),
          ...(rows.length ? [] : ['暂无数据'])].join('\n');
      }},
      yAxis: {type: 'value', scale: true, axisLabel: {formatter: display}, splitLine: {lineStyle: {color: '#eef0f3'}}},
      series: segments.map(({name, lineIndex, data}) => ({name, type: 'line', showSymbol: result.mode !== 'overview', symbolSize: 3, connectNulls: true, emphasis: {focus: 'series'},
        itemStyle: {color: palette[lineIndex % palette.length]},
        lineStyle: {color: palette[lineIndex % palette.length], width: lineIndex === (result.mode === 'spread' ? currentIndex : result.lines.length-1) ? 2.8 : 1.3},
        ...(lineIndex === currentIndex && currentPoint && data[axisIndex.get(currentPoint.x)] != null ? {markPoint:{symbol:'circle',symbolSize:7,label:{position:'top',formatter:'当前 '+display(currentPoint.value)},data:[{coord:[axisIndex.get(currentPoint.x),currentPoint.value]}]},markLine:{silent:true,symbol:'none',label:{show:false},lineStyle:{color:'#7c8590',width:1},data:[{xAxis:axisIndex.get(currentPoint.x)}]}} : {}), data})),
    };
    chart.setOption(option);
    chart.on('legendselectchanged', event => {selected = event.selected;});
  }, [result, heatmap, showAllYears]);
  return <ChartFrame><div ref={ref} className="history-chart" style={{height: heatmap ? Math.min(850, Math.max(420, result.lines.length * 12)) : height}} role="img" aria-label={result.accessible_label || (result.mode === 'spread' ? '同月份组合历年价差对照图' : heatmap ? '全合约持仓热力图' : '历年合约对比图')}/></ChartFrame>;
}

export default function HistoryPanel({product, end, near, far, priceType, revision, controls, info, title, children, view='overview'}) {
  const spreadOnly=view==='spread',mode=spreadOnly?'spread':view==='month'?'same_month':'overview';
  const [years, setYears] = usePreference('futures.history.'+view+'.years',5);
  const [month, setMonth] = usePreference('futures.history.'+view+'.'+product+'.month',Number(near?.slice(-2)) || 10);
  const nearMonth = spreadOnly ? Number(near.slice(-2)) : month;
  const farMonth = Number(far?.slice(-2)) || 1;
  const offset = near && far ? Number(far.slice(-4,-2)) - Number(near.slice(-4,-2)) : 1;
  const [metric, setMetric] = usePreference('futures.history.metric','position');
  const [overviewMetric, setOverviewMetric] = usePreference('futures.history.overviewMetric','price');
  const [overviewStyle, setOverviewStyle] = usePreference('futures.history.overviewStyle','line');
  const [result, setResult] = useState(null), [error, setError] = useState('');
  const anchorYear = mode === 'overview' ? Number(end.slice(0,4)) : 2000 + Number(near?.slice(-4,-2) || end.slice(2,4));
  const effectiveMetric = mode === 'spread' ? 'price' : mode === 'overview' ? overviewMetric : metric;
  const summary = result?.seasonal_summary;
  useEffect(() => {
    const controller = new AbortController();
    setResult(null); setError('');
    const query = new URLSearchParams({product, end, mode, years, near_month: nearMonth, far_month: farMonth, far_year_offset: offset, metric: effectiveMetric, price_type: priceType, anchor_year: anchorYear});
    fetch('/api/contract-history?' + query, {signal: controller.signal}).then(async response => {
      const data = await response.json();
      if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail));
      setResult(data);
    }).catch(e => {if (e.name !== 'AbortError') setError(e.message);});
    return () => controller.abort();
  }, [product, end, mode, years, nearMonth, farMonth, offset, effectiveMetric, priceType, anchorYear, revision]);
  const metricControl = mode === 'same_month' ? <select aria-label="对比指标" value={metric} onChange={e => setMetric(e.target.value)}><option value="position">持仓手数</option><option value="position_share">持仓占比</option><option value="price">合约价格</option><option value="volume">成交量</option></select> : mode === 'overview' ? <select aria-label="对比指标" value={overviewMetric} onChange={e => setOverviewMetric(e.target.value)}><option value="price">合约价格</option><option value="position">持仓手数</option><option value="position_share">持仓占比</option></select> : null;
  const yearsControl = <label>跨度<select aria-label="历史跨度" value={years} onChange={e => setYears(Number(e.target.value))}>{[3,5,10].map(n => <option key={n} value={n}>{n}年</option>)}</select></label>;
  return <section className="panel history-panel">
    <div className="filters history-toolbar">{title&&<h2 className="section-label">{title}</h2>}{controls?controls(spreadOnly?yearsControl:metricControl):metricControl}
      {(!spreadOnly||!controls)&&yearsControl}
      {!spreadOnly && mode !== 'overview' && <label>交割月<select aria-label="交割月份" value={month} onChange={e => setMonth(Number(e.target.value))}>{Array.from({length:12}, (_,i) => <option key={i+1} value={i+1}>{i+1}月</option>)}</select></label>}
      {mode === 'overview' && overviewMetric !== 'price' && <label>呈现方式<select value={overviewStyle} onChange={e => setOverviewStyle(e.target.value)}><option value="line">各合约曲线</option><option value="heatmap">持仓热力图</option></select></label>}
      {info}{result&&<span className="sub" title={result.missing.length?'缺少序列：'+result.missing.join('、'):undefined}>{anchorYear-years+1}—{anchorYear}{mode==='overview'?` · ${result.lines.length}合约`:result.missing.length?` · 缺${result.missing.length}年`:""}{result.meta.stale?" · 缓存降级":""}</span>}
    </div>
    {error ? <p className="error" role="alert">{error}</p> : !result ? <p className="empty" role="status">正在读取历史合约…</p> : <>
      {result.lines.length ? <HistoryChart result={result} heatmap={mode === 'overview' && effectiveMetric !== 'price' && overviewStyle === 'heatmap'}/> : <p className="empty">所选月份组合在当前数据中没有可比较序列</p>}
      {children?.(summary)}

    </>}
  </section>;
}
