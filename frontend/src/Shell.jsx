import { watchVersion } from './version-check.js';
import { selectedProduct } from './preferences.js';
import { warmNavigation } from './transport.js';
import { Component, useEffect, useRef, useState } from 'react';

const loadFutures = async () => {
  const [module] = await Promise.all([import('./futures/App.jsx'), import('./futures/styles.css')]);
  return { default: module.App };
};
const loadOptions = () => import('./Options.jsx');
const loadSeats = () => import('./seats/Dashboard.tsx');
const loadObserve = import.meta.env.OBSERVE_PREVIEW ? () => import('./Observe.jsx') : null;
const loadIntraday = import.meta.env.INTRADAY_PREVIEW ? () => import('./Intraday.jsx') : null;
// Only confirmed destinations are visible; deployment can set these at build time.
const HOME = import.meta.env.VITE_HOME_URL || 'https://mingjiec.com/';
const BASE = import.meta.env.BASE_URL;
const modules = [['seats', '席位', loadSeats], ['futures', '期货', loadFutures], ['options', '期权(Beta)', loadOptions]];
const previews = [...(loadObserve ? [['observe','观察',loadObserve]] : []), ...(loadIntraday ? [['intraday','日内',loadIntraday]] : [])];
const currentModule = () => previews.find(([id]) => [BASE+id,BASE+id+'/'].includes(location.pathname))?.[0] || modules.find(([id]) => location.pathname.startsWith(`${BASE}${id}/`) || location.pathname === `${BASE}${id}`)?.[0] || 'futures';

class ModuleErrorBoundary extends Component {
  state = { error: null };
  static getDerivedStateFromError(error) { return { error }; }
  render() {
    const error=this.state.error||this.props.error;
    return error ? <section className="module-error" role="alert"><h2>页面暂时无法加载</h2><p>{error.message}</p><button onClick={() => location.reload()}>重新加载</button></section> : this.props.children;
  }
}

export default function Shell() {
  const [active, setActive] = useState(currentModule), [entry,setEntry]=useState(0);
  const [page,setPage]=useState({});
  useEffect(()=>{
    if(page.id!==active||page.error)return;
    const code=JSON.parse(document.getElementById('navigation-code')?.textContent||'{}');
    const peers=modules.some(([id])=>id===active)?modules:previews;
    const rest=peers===modules?previews:modules;
    return warmNavigation([...peers,...rest].filter(([id])=>id!==active).map(([id])=>code[id]&&BASE+code[id]).concat(HOME));
  },[active,page.id,page.error]);
  useEffect(()=>{
    let cancelled=false;
    const load=[...modules,...previews].find(([id])=>id===active)[2];
    load().then(module=>{if(!cancelled)setPage({id:active,Component:module.default});},error=>{if(!cancelled)setPage({id:active,error});});
    return()=>{cancelled=true;};
  },[active]);
  const [versionState, setVersionState] = useState({ updated: false, failed: false });
  useEffect(() => {
    setVersionState({ updated: false, failed: false });
    if (active !== 'intraday') return watchVersion(active, setVersionState);
  }, [active]);
  const menu = useRef(null);
  const pinned = useRef(false);
  const closeMenu = () => { menu.current.open=false; pinned.current=false; };
  const lastView = useRef({});
  useEffect(() => {
    const back = () => setActive(currentModule());
    const outside = event => { if (!menu.current.contains(event.target)) closeMenu(); };
    window.addEventListener('popstate', back);
    document.addEventListener('pointerdown', outside);
    return () => { window.removeEventListener('popstate', back); document.removeEventListener('pointerdown', outside); };
  }, []);
  function select(event, next) {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (next !== active || next === 'observe') {
      lastView.current[active] = location.pathname + location.search + location.hash;
      const target = new URL(next === 'observe' ? `${BASE}${next}/` : lastView.current[next] || `${BASE}${next}/`, location.origin), product = selectedProduct();
      if (['seats','futures'].includes(next) && product && target.searchParams.get('product')?.split('.')[0] !== product) {
        target.searchParams.set('product', product);
        for (const key of ['near', 'far', 'focus_symbol']) target.searchParams.delete(key);
      }
      target.hash = '';
      history.pushState({}, '', target);
      setActive(next);
      if(next==='observe')setEntry(value=>value+1);
    }
    if (modules.some(([id]) => id === next)) menu.current.querySelector('summary').focus();
    closeMenu();
    window.scrollTo({top: 0, left: 0, behavior: 'instant'});
  }
  const ActiveModule = page.id===active?page.Component:null;
  return <div className="unified-shell">
    <script async src={new URL('site-footer.js', HOME).href} />
    <header className="unified-topbar"><nav aria-label="站点导航">
      <a className="unified-brand" href={HOME}>MINGJIE</a><a href={HOME}>策略</a>
      <details ref={menu} className="board-menu"
        onPointerEnter={event=>{if(event.pointerType==='mouse')menu.current.open=true;}}
        onPointerLeave={event=>{if(event.pointerType==='mouse'&&!pinned.current)closeMenu();}}
        onFocus={()=>{menu.current.open=true;}}
        onBlur={event=>{if(event.relatedTarget&&!event.currentTarget.contains(event.relatedTarget))closeMenu();}}
        onKeyDown={event => { if (event.key === 'Escape') { menu.current.querySelector('summary').focus(); closeMenu(); event.preventDefault(); } }}>
        <summary aria-label="切换看板" onClick={event=>{event.preventDefault();menu.current.open=true;pinned.current=true;}}>看板</summary>
        <div className="board-menu__items">{modules.map(([id, label]) => <a key={id} href={`${BASE}${id}/`} aria-current={active === id ? 'page' : undefined} onClick={event => select(event, id)}><span>{label}</span></a>)}</div>
      </details>
      {previews.map(([id,label]) => <a key={id} href={`${BASE}${id}/`} aria-current={active===id?'page':undefined} onClick={event=>select(event,id)}>{label}</a>)}
    </nav></header>
    <div className={`module-frame ${active === 'futures' ? 'futures-module' : ''}`}>
      <ModuleErrorBoundary key={active} error={page.id===active?page.error:null}>
        {ActiveModule?<ActiveModule key={entry}/>:<p className="module-loading" role="status">加载看板…</p>}
      </ModuleErrorBoundary>
    </div>
    {(versionState.updated || versionState.failed) && <div className="board-update" role="status">
      {versionState.updated && <button type="button" onClick={() => location.reload()}>数据已更新 · 更新</button>}
      {versionState.failed && <span>更新检查暂不可用</span>}
    </div>}
    <site-footer lang="zh-CN" />
  </div>;
}
