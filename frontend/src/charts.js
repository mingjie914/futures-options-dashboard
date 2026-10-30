import { createElement, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { conditionalFetch } from './transport.js';

export { usePreference } from './preferences.js';

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

// Six chart views share one engine, loaded only after a chart is mounted.
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
        import('./chart-engine.js').then(({init})=>{
          if(active.current!==state)return;
          state.chart=init(node);state.update();
          state.observer=new ResizeObserver(()=>state.chart.resize());state.observer.observe(node);
        }).catch(()=>{if(active.current===state)node.textContent='图表暂时无法加载，请刷新页面重试。';});
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

// Group members must use the same ordered date axis, including null gaps.
export function linkZoom(chart, group, syncLegend = false) {
  const apply = target => target.dispatchAction({type:'dataZoom', ...group.range}, {silent:true});
  group.add(chart);
  if (group.range) apply(chart);
  const change = event => {
    const {start, end} = event.batch?.[0] || event;
    group.range = {start, end};
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
    chart.off('datazoom', change); group.delete(chart);
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
  return createElement('div',{className:'research-metric',ref,onPointerEnter:e=>{if(e.pointerType==='mouse')show();},onPointerLeave:()=>{timer.current=setTimeout(hide,120);},onBlur:e=>{if(!e.currentTarget.contains(e.relatedTarget))hide();}},
    createElement('button',{type:'button','aria-describedby':id,onFocus:show,onClick:show},label),
    createElement('strong',{className:valueClassName},value),
    createElement('span',{ref:tip,id,role:'tooltip',className:'research-metric-tip',hidden:!open,style:position,onPointerEnter:show},hint));
}
