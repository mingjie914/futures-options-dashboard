import { Component, lazy, Suspense, useState } from "react";
import "./futures/styles.css";

const FuturesApp = lazy(async () => {
  const module = await import("./futures/App.jsx");
  return { default: module.App };
});

const OptionsApp = lazy(() => import("./Options.jsx"));

function initialModule() {
  if (typeof window !== "undefined" && window.location.pathname.startsWith("/futures")) {
    return "futures";
  }
  return "options";
}

class ModuleErrorBoundary extends Component {
  state = { error: null };

  static getDerivedStateFromError(error) {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <section className="module-error" role="alert">
          <h2>{this.props.label}暂时无法加载</h2>
          <p>{this.state.error.message || "模块发生未知错误。"}</p>
          <button type="button" onClick={this.props.onRetry}>重试模块</button>
        </section>
      );
    }
    return this.props.children;
  }
}

function ModuleLoading({ label }) {
  return <section className="module-loading" role="status">正在加载{label}…</section>;
}

export default function Shell() {
  const [active, setActive] = useState(initialModule);
  const [retrySeed, setRetrySeed] = useState(0);

  function selectModule(next) {
    setActive(next);
    setRetrySeed(0);
    if (typeof window !== "undefined") {
      const path = next === "futures" ? "/futures/" : "/options/";
      if (window.location.pathname !== path) window.history.replaceState({}, "", path);
    }
  }

  const retry = () => setRetrySeed((value) => value + 1);
  const isFutures = active === "futures";
  const moduleLabel = isFutures ? "期货研究" : "期权研究";

  return (
    <div className="unified-shell">
      <header className="unified-topbar">
        <a className="unified-brand" href={isFutures ? "/futures/" : "/options/"} onClick={(event) => { event.preventDefault(); selectModule(active); }}>
          <span className="unified-brand__mark">∿</span>
          <span><strong>RESEARCH</strong><small>统一研究终端</small></span>
        </a>
        <nav className="unified-switch" aria-label="研究模块">
          <button type="button" className={isFutures ? "is-active" : ""} aria-pressed={isFutures} onClick={() => selectModule("futures")}>期货研究</button>
          <button type="button" className={!isFutures ? "is-active" : ""} aria-pressed={!isFutures} onClick={() => selectModule("options")}>期权研究</button>
        </nav>
        <div className="unified-status" aria-label="模块数据状态">
          <span className="unified-status__badge unified-status__badge--futures">期货 · 模块内时点</span>
          <span className="unified-status__badge">历史 / 盘中时点分别展示</span>
        </div>
      </header>

      <div className={`unified-shell__content unified-shell__content--${active}`}>
        <div className={`module-frame ${isFutures ? "futures-module" : "options-module"}`}>
          <ModuleErrorBoundary key={`${active}-${retrySeed}`} label={moduleLabel} onRetry={retry}>
            <Suspense fallback={<ModuleLoading label={moduleLabel} />}>
              {isFutures ? <FuturesApp /> : <OptionsApp />}
            </Suspense>
          </ModuleErrorBoundary>
        </div>
      </div>
    </div>
  );
}
