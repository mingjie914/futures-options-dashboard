import { Component, lazy, Suspense, useEffect, useRef, useState } from 'react';

const FuturesApp = lazy(async () => {
  const [module] = await Promise.all([import('./futures/App.jsx'), import('./futures/styles.css')]);
  return { default: module.App };
});
const OptionsApp = lazy(() => import('./Options.jsx'));
// Only confirmed destinations are visible; deployment can set these at build time.
const HOME = import.meta.env.VITE_HOME_URL || 'https://mingjiec.com/';
const SEATS = import.meta.env.VITE_SEATS_URL;
const BASE = import.meta.env.BASE_URL;
const modules = [['futures', '期货', '期限结构、跨期与季节性'], ['options', '期权', '波动率、持仓与期权链']];
const currentModule = () => location.pathname.startsWith(`${BASE}futures`) ? 'futures' : 'options';

class ModuleErrorBoundary extends Component {
  state = { error: null };
  static getDerivedStateFromError(error) { return { error }; }
  render() {
    return this.state.error ? <section className="module-error" role="alert"><h2>页面暂时无法加载</h2><p>{this.state.error.message}</p><button onClick={() => location.reload()}>重新加载</button></section> : this.props.children;
  }
}

export default function Shell() {
  const [active, setActive] = useState(currentModule);
  const menu = useRef(null);
  const lastView = useRef({});
  useEffect(() => {
    const back = () => setActive(currentModule());
    const outside = event => { if (!menu.current?.contains(event.target)) menu.current?.removeAttribute('open'); };
    window.addEventListener('popstate', back);
    document.addEventListener('pointerdown', outside);
    return () => { window.removeEventListener('popstate', back); document.removeEventListener('pointerdown', outside); };
  }, []);
  function select(event, next) {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (next !== active) {
      lastView.current[active] = location.pathname + location.search;
      history.pushState({}, '', lastView.current[next] || `${BASE}${next}/`);
      setActive(next);
    }
    menu.current.querySelector('summary').focus();
    menu.current.open = false;
  }
  return <div className="unified-shell">
    <header className="unified-topbar"><nav aria-label="站点导航">
      <a className="unified-brand" href={HOME}>MINGJIE</a><a href={HOME}>策略</a>
      <details ref={menu} className="board-menu"
        onPointerEnter={event=>{if(event.pointerType==='mouse')menu.current.open=true;}}
        onPointerLeave={event=>{if(event.pointerType==='mouse')menu.current.open=false;}}
        onFocus={()=>{menu.current.open=true;}}
        onBlur={event=>{if(!event.currentTarget.contains(event.relatedTarget))menu.current.open=false;}}
        onKeyDown={event => { if (event.key === 'Escape') { menu.current.querySelector('summary').focus(); menu.current.open=false; event.preventDefault(); } }}>
        <summary aria-label="切换看板" className="is-active" onClick={event=>{event.preventDefault();menu.current.open=true;}}>看板 <svg aria-hidden="true" width="12" height="12" viewBox="0 0 12 12"><path d="m3 4.5 3 3 3-3" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round"/></svg></summary>
        <div className="board-menu__items">{modules.map(([id, label, description]) => <a key={id} href={`${BASE}${id}/`} aria-current={active === id ? 'page' : undefined} onClick={event => select(event, id)}><span>{label}</span><small>{description}</small></a>)}{SEATS && <a href={SEATS}><span>席位</span><small>会员持仓与变化</small></a>}</div>
      </details>
    </nav></header>
    <div className={`module-frame ${active === 'futures' ? 'futures-module' : ''}`}>
      <ModuleErrorBoundary key={active}><Suspense fallback={<p className="module-loading" role="status">加载看板…</p>}>
        {active === 'futures' ? <FuturesApp /> : <OptionsApp />}
      </Suspense></ModuleErrorBoundary>
    </div>
  </div>;
}
