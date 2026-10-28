import { createElement, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';

// Keep display preferences in this browser tab; never cache market data.
export function usePreference(key, initial) {
  const storageKey = 'dashboard.ui.v1.' + key;
  const read = () => {
    try { const value = sessionStorage.getItem(storageKey); if (value !== null) return JSON.parse(value); } catch {}
    return typeof initial === 'function' ? initial() : initial;
  };
  const [saved, setSaved] = useState(() => ({key, value: read()}));
  const current = saved.key === key ? saved : {key, value: read()};
  if (saved.key !== key) setSaved(current);
  useEffect(() => {
    try { sessionStorage.setItem(storageKey, JSON.stringify(current.value)); } catch {}
  }, [storageKey, current.value]);
  return [current.value, value => setSaved(previous => ({key, value: typeof value === 'function' ? value(previous.value) : value}))];
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
export function useChart(ref, draw, dependencies) {
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    let cancelled = false, chart, observer;
    const visibility = new IntersectionObserver(entries => {
      if (!entries.some(entry => entry.isIntersecting)) return;
      visibility.disconnect();
      import('./chart-engine.js').then(({ init }) => {
        if (cancelled) return;
        chart = init(node);
        draw(chart);
        observer = new ResizeObserver(() => chart.resize());
        observer.observe(node);
      }).catch(() => {
        if (!cancelled) node.textContent = '图表暂时无法加载，请刷新页面重试。';
      });
    }, { rootMargin: '200px' });
    visibility.observe(node);
    return () => { cancelled = true; visibility.disconnect(); observer?.disconnect(); chart?.dispose(); };
  }, dependencies);
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
