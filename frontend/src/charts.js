import { createElement, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { conditionalFetch } from './transport.js';

export { usePreference } from './preferences.js';

export function axisTooltip(params,format=String,heading) {
  const items=Array.isArray(params)?params:[params];
  const lines=items.flatMap(p=>{const value=Array.isArray(p.value)?p.value.at(-1):p.value;return value==null||value===''||!Number.isFinite(Number(value))?[]:[`${p.marker||''} ${p.seriesName}：${format(value,p)}`];});
  return lines.length?[heading??items[0]?.axisValueLabel??items[0]?.axisValue??items[0]?.name??'',...lines].join('\n'):'';
}

export function observedRows(rows, keys) {
  const valid = row => keys.some(key => Number.isFinite(row[key]));
  const first = rows.findIndex(valid);
  return first < 0 ? [] : rows.slice(first, rows.findLastIndex(valid) + 1);
}

// Keep the last matching view in place while its next selection is fetched.
export function useResource(url, revision=0, scope='') {
  const [result,setResult]=useState({data:null});
  const busy=!!url&&(result.url!==url||result.revision!==revision||result.scope!==scope);
  useEffect(()=>{
    if(!url)return;
    const controller=new AbortController();
    conditionalFetch(url,{signal:controller.signal}).then(async response=>{
      const data=await response.json();
      if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail||'数据读取失败'));
      if(!controller.signal.aborted)setResult({url,revision,scope,data});
    }).catch(error=>{if(!controller.signal.aborted)setResult({url,revision,scope,data:null,error:error.message});});
    return()=>controller.abort();
  },[url,revision,scope]);
  return {data:url&&result.scope===scope?result.data:null,error:url&&!busy&&result.scope===scope?result.error:'',busy};
}

export function RefreshButton({busy=false, onClick, type='button'}) {
  return createElement('button', {className:'board-refresh', type, onClick, disabled:busy},
    createElement('span', {'aria-hidden':true}, busy ? '◌' : '↻'), busy ? '更新中' : '刷新');
}

export function ChartFrame({children}) {
  const ref=useRef(), [expanded,setExpanded]=useState(false), [error,setError]=useState('');
  useEffect(()=>{const change=()=>setExpanded(document.fullscreenElement===ref.current);document.addEventListener('fullscreenchange',change);return()=>document.removeEventListener('fullscreenchange',change);},[]);
  const toggle=async()=>{try{setError('');if(expanded)await document.exitFullscreen();else await ref.current.requestFullscreen();}catch{setError('此浏览器不支持图表全屏，可使用图内缩放。');}};
  return createElement('div',{className:'research-chart-frame',ref},
    createElement('button',{type:'button',className:'research-chart-expand',onClick:toggle,'aria-label':expanded?'还原图表':'放大图表'},expanded?'还原 ↙':'放大 ↗'),
    error&&createElement('p',{role:'status'},error),children);
}

// Charts share one engine, loaded only near the viewport.
export function useChart(ref, draw, dependencies, zoomGroup, syncLegend = false) {
  const active=useRef();
  useEffect(() => {
    const node = ref.current;
    if(active.current?.node!==node){active.current?.dispose();active.current=null;}
    if(!node)return;
    let state=active.current;
    if(!state){
      state={node};active.current=state;
      const visibility=new IntersectionObserver(entries=>{
        if(!entries.some(entry=>entry.isIntersecting))return;
        visibility.disconnect();
        node.ariaBusy='true';
        import('./chart-engine.js').then(({init})=>{
          if(active.current!==state)return;
          state.chart=init(node);state.update();
          state.observer=new ResizeObserver(()=>{const width=node.clientWidth,height=node.clientHeight;if(state.chart.getWidth()!==width||state.chart.getHeight()!==height)state.chart.resize({width,height});});state.observer.observe(node);
        }).catch(()=>{if(active.current===state)node.textContent='图表暂时无法加载，请刷新页面重试。';}).finally(()=>{node.ariaBusy='false';});
      },{rootMargin:'200px'});
      state.dispose=()=>{visibility.disconnect();state.observer?.disconnect();state.unlink?.();state.chart?.dispose();};
      visibility.observe(node);
    }
    const inputs=[...dependencies,zoomGroup,syncLegend];
    if(!state.inputs||inputs.length!==state.inputs.length||inputs.some((value,i)=>!Object.is(value,state.inputs[i]))){
      state.inputs=inputs;
      state.update=()=>{state.unlink?.();draw(state.chart);state.unlink=zoomGroup?linkZoom(state.chart,zoomGroup,syncLegend):null;};
      if(state.chart)state.update();
    }
  });
  useEffect(()=>()=>{active.current?.dispose();active.current=null;},[]);
}

// Percent groups share an axis; byDate groups may start on different dates.
export function linkZoom(chart, group, syncLegend = false) {
  const axes=group.byDate?(group.dateAxes ||= new Map()):null;
  if(axes)axes.set(chart,chart.getOption().xAxis[0].data);
  const apply = target => {
    let range=group.range;
    if(axes){
      const axis=axes.get(target),clamp=d=>d<axis[0]?axis[0]:d>axis.at(-1)?axis.at(-1):d;
      range={startValue:clamp(range.startValue),endValue:clamp(range.endValue)};
    }
    target.dispatchAction({type:'dataZoom',...range},{silent:true});
  };
  group.add(chart);
  if (group.range) apply(chart);
  const change = event => {
    const {start, end} = event.batch?.[0] || event;
    group.range = {start, end};
    if(axes){
      const {dataZoom}=chart.getOption(),axis=axes.get(chart);
      group.range={startValue:axis[dataZoom[0].startValue],endValue:axis[dataZoom[0].endValue]};
    }
    for (const target of group) if (target !== chart) apply(target);
  };
  chart.on('datazoom', change);
  const legend = event => {
    group.selected = event.selected;
    for (const target of group.legendCharts) if (target !== chart) applyLegend(target);
  };
  const applyLegend = target => {
    for (const [name, selected] of Object.entries(group.selected || {}))
      target.dispatchAction({type: selected ? 'legendSelect' : 'legendUnSelect', name}, {silent:true});
  };
  if (syncLegend) {
    (group.legendCharts ||= new Set()).add(chart);
    applyLegend(chart);
    chart.on('legendselectchanged', legend);
  }
  return () => {
    chart.off('datazoom', change); group.delete(chart);axes?.delete(chart);
    if (syncLegend) { chart.off('legendselectchanged', legend); group.legendCharts.delete(chart); }
  };
}

// Shared by futures and options: the name opens the same compact explanation.
export function Metric({label, value, hint, valueClassName}) {
  const id=useId(), ref=useRef(), tip=useRef(), timer=useRef(), [open,setOpen]=useState(false), [position,setPosition]=useState({});
  const show=()=>{clearTimeout(timer.current);setOpen(true);};
  const hide=()=>{clearTimeout(timer.current);setOpen(false);};
  useLayoutEffect(()=>{
    if(!open)return;
    const box=ref.current.getBoundingClientRect(), content=tip.current.getBoundingClientRect();
    setPosition({left:Math.max(12,Math.min(innerWidth-content.width-12,box.left)),top:Math.max(12,Math.min(innerHeight-content.height-12,box.top>content.height+20?box.top-content.height-8:box.bottom+8))});
  },[open,hint]);
  useEffect(()=>{
    if(!open)return;
    const outside=e=>{if(!ref.current.contains(e.target))hide();},escape=e=>{if(e.key==='Escape')hide();};
    document.addEventListener('pointerdown',outside);document.addEventListener('keydown',escape);
    window.addEventListener('scroll',hide,true);window.addEventListener('resize',hide);
    return()=>{document.removeEventListener('pointerdown',outside);document.removeEventListener('keydown',escape);window.removeEventListener('scroll',hide,true);window.removeEventListener('resize',hide);};
  },[open]);
  useEffect(()=>()=>clearTimeout(timer.current),[]);
  if (!hint) return createElement('div',{className:'research-metric'},createElement('span',{className:'research-metric-label'},label),createElement('strong',{className:valueClassName},value));
  return createElement('div',{className:'research-metric',ref,onPointerEnter:e=>{if(e.pointerType==='mouse')show();},onPointerLeave:()=>{timer.current=setTimeout(hide,120);},onBlur:e=>{if(!e.currentTarget.contains(e.relatedTarget))hide();}},
    createElement('button',{type:'button','aria-describedby':id,onFocus:show,onClick:show},label),
    createElement('strong',{className:valueClassName},value),
    createElement('span',{ref:tip,id,role:'tooltip',className:'research-metric-tip',hidden:!open,style:position,onPointerEnter:show},hint));
}
