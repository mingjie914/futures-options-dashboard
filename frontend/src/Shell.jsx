import { watchVersion } from './version-check.js';
import { selectedProduct } from './preferences.js';
import { Component, lazy, Suspense, useEffect, useRef, useState } from 'react';

const FuturesApp = lazy(async () => {
  const [module] = await Promise.all([import('./futures/App.jsx'), import('./futures/styles.css')]);
  return { default: module.App };
});
const OptionsApp = lazy(() => import('./Options.jsx'));
const SeatsApp = lazy(() => import('./seats/Dashboard.tsx'));
// Only confirmed destinations are visible; deployment can set these at build time.
const HOME = import.meta.env.VITE_HOME_URL || 'https://mingjiec.com/';
const BASE = import.meta.env.BASE_URL;
const touchNavigation = '(hover: none), (pointer: coarse)';
const modules = [['seats', '席位', SeatsApp], ['futures', '期货', FuturesApp], ['options', '期权(Beta)', OptionsApp]];
const currentModule = () => modules.find(([id]) => location.pathname.startsWith(`${BASE}${id}/`) || location.pathname === `${BASE}${id}`)?.[0] || 'futures';

class ModuleErrorBoundary extends Component {
  state = { error: null };
  static getDerivedStateFromError(error) { return { error }; }
  render() {
    return this.state.error ? <section className="module-error" role="alert"><h2>页面暂时无法加载</h2><p>{this.state.error.message}</p><button onClick={() => location.reload()}>重新加载</button></section> : this.props.children;
  }
}

export default function Shell() {
  const [active, setActive] = useState(currentModule);
  const [touch, setTouch] = useState(() => matchMedia(touchNavigation).matches);
  const [versionState, setVersionState] = useState({ updated: false, failed: false });
  useEffect(() => {
    setVersionState({ updated: false, failed: false });
    return watchVersion(active, setVersionState);
  }, [active]);
  const menu = useRef(null);
  const lastView = useRef({});
  useEffect(() => {
    const media = matchMedia(touchNavigation);
    const inputChanged = () => { setTouch(media.matches); menu.current.open = false; };
    media.addEventListener('change', inputChanged);
    const back = () => setActive(currentModule());
    const outside = event => { if (!menu.current?.contains(event.target)) menu.current?.removeAttribute('open'); };
    window.addEventListener('popstate', back);
    document.addEventListener('pointerdown', outside);
    return () => { media.removeEventListener('change', inputChanged); window.removeEventListener('popstate', back); document.removeEventListener('pointerdown', outside); };
  }, []);
  function select(event, next) {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (next !== active) {
      lastView.current[active] = location.pathname + location.search + location.hash;
      const target = new URL(lastView.current[next] || `${BASE}${next}/`, location.origin), product = selectedProduct();
      if (next !== 'options' && product && target.searchParams.get('product')?.split('.')[0] !== product) {
        target.searchParams.set('product', product);
        for (const key of ['near', 'far', 'focus_symbol']) target.searchParams.delete(key);
      }
      target.hash = '';
      history.pushState({}, '', target);
      setActive(next);
    }
    menu.current.querySelector(touch ? 'summary' : 'summary a').focus();
    menu.current.open = false;
    window.scrollTo({top: 0, left: 0, behavior: 'instant'});
  }
  const ActiveModule = modules.find(([id]) => id === active)[2];
  return <div className="unified-shell">
    <script async src={new URL('site-footer.js', HOME).href} />
    <header className="unified-topbar"><nav aria-label="站点导航">
      <a className="unified-brand" href={HOME}>MINGJIE</a><a href={HOME}>策略</a>
      <details ref={menu} className="board-menu"
        onPointerEnter={event=>{if(!touch&&event.pointerType==='mouse')menu.current.open=true;}}
        onPointerLeave={event=>{if(!touch&&event.pointerType==='mouse')menu.current.open=false;}}
        onFocus={()=>{if(!touch)menu.current.open=true;}}
        onBlur={event=>{if(event.relatedTarget&&!event.currentTarget.contains(event.relatedTarget))menu.current.open=false;}}
        onKeyDown={event => { if (event.key === 'Escape') { menu.current.querySelector(touch ? 'summary' : 'summary a').focus(); menu.current.open=false; event.preventDefault(); } }}>
        <summary tabIndex={touch ? undefined : -1} aria-label={touch ? '切换看板' : undefined}>{touch ? '看板' : <a href={`${BASE}${modules[0][0]}/`} aria-label="切换看板" onClick={event=>select(event, modules[0][0])}>看板</a>}</summary>
        <div className="board-menu__items">{modules.map(([id, label]) => <a key={id} href={`${BASE}${id}/`} aria-current={active === id ? 'page' : undefined} onClick={event => select(event, id)}><span>{label}</span></a>)}</div>
      </details>
    </nav></header>
    <div className={`module-frame ${active === 'futures' ? 'futures-module' : ''}`}>
      <ModuleErrorBoundary key={active}><Suspense fallback={<p className="module-loading" role="status">加载看板…</p>}>
        <ActiveModule />
      </Suspense></ModuleErrorBoundary>
    </div>
    {(versionState.updated || versionState.failed) && <div className="board-update" role="status">
      {versionState.updated && <button type="button" onClick={() => location.reload()}>数据已更新 · 更新</button>}
      {versionState.failed && <span>更新检查暂不可用</span>}
    </div>}
    <site-footer lang="zh-CN" />
  </div>;
}
