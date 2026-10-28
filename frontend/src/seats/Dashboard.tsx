import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
} from "react";
import styles from "./dashboard.module.css";
import { defaultAdapter as adapter, marketGroups } from "./adapter";
import { usePreference, selectedProduct, rememberProduct } from "../preferences.js";
import type {
  AnomalyRow,
  CombinationRow,
  DashboardFilters,
  DashboardSnapshot,
  DashboardView,
  DataState,
  MetricCardData,
  NetPositionRow,
  Scope,
  SeatHistory,
  SeatRankingRow,
  SeatTag,
  TermChangeRow,
  TermContractRow,
  Tone,
  ValidationRuleRow,
} from "./types";

const views: Array<{ id: DashboardView; label: string }> = [
  { id: "combination", label: "联合席位" },
  { id: "market", label: "市场信号" },
  { id: "term", label: "跨期结构" },
];

const toneClasses: Record<Tone, string> = {
  accent: styles.toneAccent,
  gold: styles.toneGold,
  positive: styles.tonePositive,
  negative: styles.toneNegative,
  warning: styles.toneWarning,
  neutral: styles.toneNeutral,
};

const scopeLabels: Record<Scope, string> = {
  all: "全部席位",
  main: "主力席位",
  retail: "散户席位",
};

const stateCopy: Record<
  DataState,
  { label: string; detail: string; icon: string; tone: Tone }
> = {
  loading: { label: "正在加载数据", detail: "正在读取当前批次，请稍候。", icon: "◌", tone: "accent" },
  ready: { label: "数据已就绪", detail: "当前批次已通过完整性校验。", icon: "●", tone: "positive" },
  empty: { label: "暂无数据", detail: "当前筛选条件没有可展示的数据。", icon: "○", tone: "neutral" },
  partial: { label: "数据部分可用", detail: "部分品种或席位尚未完成更新。", icon: "△", tone: "warning" },
  stale: { label: "快照较旧", detail: "距离上次发布已超过 48 小时，请核对所选交易日。", icon: "◷", tone: "warning" },
  error: { label: "数据读取失败", detail: "暂时无法读取看板数据，请稍后重试。", icon: "!", tone: "negative" },
};

const numberFormatter = new Intl.NumberFormat("zh-CN");

function formatNumber(value: number): string {
  return numberFormatter.format(Math.round(value));
}

function formatCompact(value: number): string {
  const absolute = Math.abs(value);
  if (absolute >= 10000) return `${(value / 10000).toFixed(1)}万`;
  return formatNumber(value);
}

function formatSigned(value: number): string {
  return `${value >= 0 ? "+" : "−"}${formatCompact(Math.abs(value))}`;
}

function sumSignedValues(values: number[]) {
  let positive = 0, negative = 0;
  for (const value of values) {
    if (value > 0) positive += value;
    else negative -= value;
  }
  return { positive, negative, net: positive - negative };
}

function formatPercent(value: number, digits = 1): string {
  return `${value.toFixed(digits)}%`;
}

function formatConfidence(value: number | null | undefined): string {
  return value == null ? "不可用" : formatPercent(value * 100, 0);
}

function readFilters(): DashboardFilters {
  const params = new URLSearchParams(window.location.search);
  const scope = params.get("scope");
  return { productCode: params.get("product") && params.get("product") !== "all" ? params.get("product")! : selectedProduct("RB.SHFE"), tradeDate: params.get("date") || "",
    scope: scope === "main" || scope === "retail" ? scope : "all" };
}

const readView = () => { const view = new URLSearchParams(window.location.search).get("view"); return view === "validation" ? "term" : views.find((item) => item.id === view)?.id ?? "combination"; };

export function Dashboard() {
  const [view, setView] = useState<DashboardView>(readView);
  useEffect(() => {
    const restoreView = () => {
      setView(readView());
      setFilters(readFilters());
    };
    window.addEventListener("popstate", restoreView);
    return () => window.removeEventListener("popstate", restoreView);
  }, [readView]);
  useEffect(() => {
    const url = new URL(window.location.href);
    if (url.searchParams.get("view") === "validation") url.hash = "history-comparison";
    url.searchParams.set("view", view);
    if (url.href !== window.location.href) window.history.replaceState(null, "", url);
  }, [view]);
  const [filters, setFilters] = useState<DashboardFilters>(readFilters);
  useEffect(() => {
    const url = new URL(window.location.href);
    url.searchParams.set("product", filters.productCode);
    rememberProduct(filters.productCode);
    for (const [key, value] of [["date", filters.tradeDate], ["scope", filters.scope === "all" ? "" : filters.scope]]) {
      if (value) url.searchParams.set(key, value); else url.searchParams.delete(key);
    }
    if (url.href !== window.location.href) window.history.replaceState(null, "", url);
  }, [filters]);
  const productCode = view === "term" ? filters.productCode : "all";
  const requestFilters = useMemo(() => ({ productCode, tradeDate: filters.tradeDate, scope: filters.scope }), [productCode, filters.tradeDate, filters.scope]);
  const [loadedView, setLoadedView] = useState(view);
  const [snapshot, setSnapshot] = useState<DashboardSnapshot | null>(null);
  const [loadState, setLoadState] = useState<DataState>("loading");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [selectedTag, setSelectedTag] = useState<SeatTag | null>(null);
  const [history, setHistory] = useState<SeatHistory | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [refreshVersion, setRefreshVersion] = useState(0);
  const historyRequest = useRef<AbortController | null>(null);
  const closeHistory = useCallback(() => {
    historyRequest.current?.abort();
    setHistory(null);
    setHistoryLoading(false);
  }, []);
  useEffect(() => () => historyRequest.current?.abort(), []);

  useEffect(() => {
    const controller = new AbortController();
    setIsRefreshing(true);
    setLoadState("loading");
    setErrorMessage(null);
    void adapter
      .loadSnapshot(requestFilters, controller.signal, view)
      .then((nextSnapshot) => {
        if (!controller.signal.aborted) {
          setSnapshot(nextSnapshot);
          setLoadedView(view);
          setLoadState(nextSnapshot.meta.state);
        }
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        if (!controller.signal.aborted) {
          setLoadState("error");
          setErrorMessage(error instanceof Error ? error.message : "数据请求失败");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setIsRefreshing(false);
      });
    return () => controller.abort();
  }, [adapter, requestFilters, view, refreshVersion]);

  useEffect(closeHistory, [closeHistory, requestFilters, view]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 3200);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const refresh = useCallback(() => setRefreshVersion((version) => version + 1), []);

  const openHistory = useCallback(
    async (seatId: string, productCode?: string) => {
      historyRequest.current?.abort();
      const controller = new AbortController();
      historyRequest.current = controller;
      setHistoryLoading(true);
      setHistory(null);
      try {
        const nextHistory = await adapter.loadSeatHistory(seatId, controller.signal, productCode);
        if (!controller.signal.aborted) setHistory(nextHistory);
      } catch (error: unknown) {
        if (!controller.signal.aborted) setToast(error instanceof Error ? error.message : "历史数据暂不可用");
      } finally {
        if (!controller.signal.aborted) setHistoryLoading(false);
      }
    },
    [adapter],
  );

  const changeView = useCallback((nextView: DashboardView) => {
    const url = new URL(window.location.href);
    url.searchParams.set("view", nextView);
    url.hash = "";
    if (url.href !== window.location.href) window.history.pushState(null, "", url);
    setView(nextView);
  }, []);

  const openProduct = useCallback((productCode: string) => {
    setFilters((current) => ({ ...current, productCode }));
    changeView("term");
  }, [changeView]);

  const handleTabKeyDown = useCallback(
    (event: KeyboardEvent<HTMLButtonElement>) => {
      if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
      event.preventDefault();
      const currentIndex = views.findIndex((item) => item.id === view);
      const direction = event.key === "ArrowRight" ? 1 : -1;
      const nextIndex = (currentIndex + direction + views.length) % views.length;
      changeView(views[nextIndex].id);
      document.getElementById(`dashboard-tab-${views[nextIndex].id}`)?.focus();
    },
    [view, changeView],
  );

  return (
    <div className={styles.dashboard}>
      <HoverHelp />
      <a className={styles.skipLink} href="#dashboard-main">
        跳到主要内容
      </a>

      <main className="board-content" id="dashboard-main" aria-label="期货席位看板" aria-busy={isRefreshing}>

        <nav className="board-tabs" role="tablist" aria-label="席位看板视图">
          {views.map((item) => (
            <button
              id={`dashboard-tab-${item.id}`}
              key={item.id}
              type="button"
              role="tab"
              aria-selected={view === item.id}
              aria-controls={`dashboard-panel-${item.id}`}
              tabIndex={view === item.id ? 0 : -1}
              onClick={() => changeView(item.id)}
              onKeyDown={handleTabKeyDown}
            >
              <span>{item.label}</span>
            </button>
          ))}
        </nav>

        <FilterBar
          showProduct={view === "term"}
          filters={filters}
          products={snapshot?.products.filter((product) => product.code !== "all") ?? []}
          isRefreshing={isRefreshing}
          onFilterChange={setFilters}
          onRefresh={refresh}
          snapshot={snapshot}
        />

        {loadState !== "ready" && (
          <StateNotice state={loadState} detail={errorMessage ? `${errorMessage}${snapshot && loadedView === view ? "；下方保留上次成功读取的数据" : ""}` : undefined} onRetry={loadState === "error" ? refresh : undefined} />
        )}
        {snapshot?.meta.warnings.map((warning) => (
          <div className={styles.warningStrip} key={warning} role="status">
            <span className={styles.warningIcon} aria-hidden="true">△</span>
            <span>{warning}</span>
          </div>
        ))}

        {(!snapshot || loadedView !== view) && loadState === "loading" && <DashboardSkeleton />}
        {(!snapshot || loadedView !== view) && loadState !== "loading" && <EmptyPageState state={loadState} onRetry={refresh} />}
        {snapshot && loadedView === view && (
          <div className={styles.viewPanel} id={`dashboard-panel-${view}`} role="tabpanel" aria-labelledby={`dashboard-tab-${view}`}>
            {view === "combination" && (
              <CombinationView
                snapshot={snapshot}
                onSeat={openHistory}
                onTag={setSelectedTag}
                onProduct={openProduct}
              />
            )}
            {view === "market" && (
              <MarketView
                snapshot={snapshot}
                scope={filters.scope}
                onSeat={openHistory}
                onTag={setSelectedTag}
                onProduct={openProduct}
              />
            )}
            {view === "term" && (
              <TermView snapshot={snapshot} onSeat={openHistory} onTag={setSelectedTag} />
            )}
          </div>
        )}
      </main>

      {selectedTag && <TagInspector tag={selectedTag} onClose={() => setSelectedTag(null)} />}
      {(history || historyLoading) && (
        <HistoryDrawer history={history} loading={historyLoading} onClose={closeHistory} />
      )}
      {toast && (
        <div className={styles.toast} role="status" aria-live="polite">
          <span className={styles.toastMark} aria-hidden="true">✓</span>
          {toast}
        </div>
      )}
    </div>
  );
}

interface FilterBarProps {
  showProduct: boolean;
  filters: DashboardFilters;
  products: DashboardSnapshot["products"];
  isRefreshing: boolean;
  snapshot: DashboardSnapshot | null;
  onFilterChange: React.Dispatch<React.SetStateAction<DashboardFilters>>;
  onRefresh: () => void;
}

function FilterBar({
  showProduct,
  filters,
  products,
  isRefreshing,
  snapshot,
  onFilterChange,
  onRefresh,
}: FilterBarProps) {
  const [productQuery, setProductQuery] = useState<string | null>(null);
  const [activeProduct, setActiveProduct] = useState(0);
  const productList = useRef<HTMLUListElement>(null);
  const productLabel = (product: DashboardSnapshot["products"][number]) => `${product.code.split(".")[0].toUpperCase()}/${product.label}`;
  const selectedProduct = products.find((product) => product.code.split(".")[0] === filters.productCode.split(".")[0]);
  const matches = products.filter((product) => `${product.code} ${productLabel(product)}`.toLowerCase().includes((productQuery ?? "").trim().toLowerCase()));
  const openProducts = () => {
    setProductQuery("");
    setActiveProduct(Math.max(0, products.findIndex((product) => product.code === selectedProduct?.code)));
  };
  const selectProduct = (productCode: string) => {
    onFilterChange((current) => ({ ...current, productCode }));
    setProductQuery(null);
  };
  useEffect(() => {
    if (productQuery !== null) productList.current?.querySelector('[data-active="true"]')?.scrollIntoView({ block: "nearest" });
  }, [activeProduct, productQuery]);
  return (
    <section className={styles.filterBar} aria-label="看板筛选与操作">
      <div className={styles.filterFields}>
        {showProduct && <div className={`${styles.filterField} ${styles.productPicker}`}>
          <label htmlFor="product-search">品种</label>
          <input id="product-search" role="combobox" aria-label="选择品种" aria-autocomplete="list" aria-expanded={productQuery !== null} aria-controls="product-options" aria-activedescendant={productQuery !== null && matches[activeProduct] ? `product-option-${activeProduct}` : undefined}
            value={productQuery ?? (selectedProduct ? productLabel(selectedProduct) : filters.productCode)}
            placeholder={selectedProduct ? productLabel(selectedProduct) : "输入代码或中文名称"} autoComplete="off" spellCheck={false} disabled={!products.length}
            onFocus={openProducts} onClick={() => { if (productQuery === null) openProducts(); }} onBlur={() => setProductQuery(null)}
            onChange={(event) => { setProductQuery(event.target.value); setActiveProduct(0); }}
            onKeyDown={(event) => {
              if (event.nativeEvent.isComposing) return;
              if (event.key === "Escape") { event.preventDefault(); setProductQuery(null); }
              else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                if (productQuery === null) openProducts();
                else if (matches.length) setActiveProduct((index) => (index + (event.key === "ArrowDown" ? 1 : -1) + matches.length) % matches.length);
              } else if (event.key === "Enter" && productQuery !== null) {
                event.preventDefault();
                if (matches[activeProduct]) selectProduct(matches[activeProduct].code);
              }
            }} />
          <span className={styles.productChevron} aria-hidden="true">⌄</span>
          {productQuery !== null && <ul ref={productList} id="product-options" role="listbox" aria-label="匹配品种" className={styles.productOptions}>
            {matches.map((product, index) => <li key={product.code} id={`product-option-${index}`} role="option" aria-selected={product.code === selectedProduct?.code} data-active={index === activeProduct} onMouseDown={(event) => event.preventDefault()} onClick={() => selectProduct(product.code)}><span>{productLabel(product)}</span><small>{product.exchange}</small></li>)}
            {!matches.length && <li role="presentation" className={styles.productNoMatch}>没有匹配的品种</li>}
          </ul>}
        </div>}
        <label className={styles.filterField}>
          <span>交易日</span>
          <input
            type="date"
            value={filters.tradeDate || snapshot?.meta.tradeDate || ""}
            aria-label="选择交易日"
            onChange={(event) => onFilterChange((current) => ({ ...current, tradeDate: event.target.value }))}
          />
        </label>
        {snapshot?.overview.hasSeatGroups && <label className={styles.filterField}>
          <span>席位分组</span>
          <select
            value={filters.scope}
            aria-label="选择观察范围"
            onChange={(event) => onFilterChange((current) => ({ ...current, scope: event.target.value as Scope }))}
          >
            <option value="all">全部席位</option>
            <option value="main">主力席位</option>
            <option value="retail">散户席位</option>
          </select>
        </label>}
      </div>
      <div className={styles.filterActions}>
        {snapshot && <SnapshotStatus meta={snapshot.meta} />}
        <button className={styles.primaryAction} type="button" onClick={onRefresh} disabled={isRefreshing} data-tooltip="按当前筛选检查本页已发布数据；其他页切换时检查。">
          <span aria-hidden="true">{isRefreshing ? "◌" : "↻"}</span>
          {isRefreshing ? "更新中" : "刷新"}
        </button>
      </div>
    </section>
  );
}

function SnapshotStatus({ meta }: { meta: DashboardSnapshot["meta"] }) {
  const state = meta.state === "ready" ? "positive" : meta.state === "stale" ? "warning" : "negative";
  if (meta.state === "ready") return null;
  return (
    <div className={styles.snapshotStatus}>
      <span className={`${styles.statusDot} ${toneClasses[state]}`} aria-hidden="true" />
      <span>{meta.statusLabel}</span>
    </div>
  );
}

function StateNotice({ state, detail, onRetry }: { state: DataState; detail?: string; onRetry?: () => void }) {
  const copy = stateCopy[state];
  return (
    <div className={`${styles.stateNotice} ${toneClasses[copy.tone]}`} role={state === "error" ? "alert" : "status"}>
      <span className={styles.stateIcon} aria-hidden="true">{copy.icon}</span>
      <div>
        <strong>{copy.label}</strong>
        <span>{detail ?? copy.detail}</span>
      </div>
      {onRetry && <button className={styles.noticeButton} type="button" onClick={onRetry}>重新读取</button>}
    </div>
  );
}

function DashboardSkeleton() {
  return (
    <div className={styles.skeletonGrid} aria-label="正在加载看板">
      <div className={`${styles.skeletonBlock} ${styles.skeletonLarge}`} />
      <div className={styles.skeletonColumn}>
        <div className={styles.skeletonBlock} />
        <div className={styles.skeletonBlock} />
      </div>
      <div className={`${styles.skeletonBlock} ${styles.skeletonWide}`} />
    </div>
  );
}

function EmptyPageState({ state, onRetry }: { state: DataState; onRetry: () => void }) {
  const copy = stateCopy[state];
  return (
    <div className={styles.emptyPageState}>
      <div className={styles.emptyGlyph} aria-hidden="true">{state === "error" ? "!" : "—"}</div>
      <h2>{copy.label}</h2>
      <p>{copy.detail}</p>
      <button className={styles.primaryAction} type="button" onClick={onRetry}>重新读取</button>
    </div>
  );
}

function CombinationView({
  snapshot,
  onSeat,
  onTag,
  onProduct,
}: {
  snapshot: DashboardSnapshot;
  onSeat: (seatId: string) => void;
  onTag: (tag: SeatTag) => void;
  onProduct: (productCode: string) => void;
}) {
  return (
    <div className={styles.pageStack}>

      <CombinationMatrix
        combinations={snapshot.combinations}
        rows={snapshot.combinationMatrix}
        onProduct={onProduct}
      />

      <section className={styles.productSection} aria-label={`${snapshot.meta.selectedProductLabel}席位概览`}>
      <div className={`${styles.twoColumnGrid} ${styles.comparisonGrid}`}>
        <NetPositionPanel rows={snapshot.netTop} onSeat={onSeat} onTag={onTag} />
        <DivergenceCards cards={snapshot.divergenceCards} pair={snapshot.divergencePair} onProduct={onProduct} />
      </div>

      <div className={styles.twoColumnGrid}>
        <SeatRankingPanel title="多头 Top20" rows={snapshot.longTop} onSeat={onSeat} onTag={onTag} />
        <SeatRankingPanel title="空头 Top20" rows={snapshot.shortTop} onSeat={onSeat} onTag={onTag} />
      </div>
      </section>
    </div>
  );
}

function DivergenceCards({ cards, pair, onProduct }: { cards: DashboardSnapshot["divergenceCards"]; pair?: string; onProduct: (productCode: string) => void }) {
  if (!cards.length) return null;
  return (
    <section className={styles.panel} aria-label="席位背离">
      <PanelHeader title="席位背离" subtitle={pair || "资金突击队 × 卧龙凤雏"} />
      <div className={styles.divergenceCards}>
        {cards.map((card) => (
          <article className={`${styles.divergenceSignalCard} ${toneClasses[card.tone]}`} key={card.code}>
            <div><span className={styles.signalCountDot} aria-hidden="true" /><strong>{card.label}</strong><b>{card.count}</b></div>
            <div className={styles.divergenceProducts}>
              {card.products.length
                ? card.products.map((product) => <button type="button" key={product.code} onClick={() => onProduct(product.code)}>{product.label}</button>)
                : <span>无已发布信号</span>}
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

function ResearchSummary({ snapshot }: { snapshot: DashboardSnapshot }) {
  const sectionRef = useRef<HTMLElement>(null);
  useEffect(() => {
    if (window.location.hash === "#history-comparison") sectionRef.current?.scrollIntoView({ block: "start" });
  }, []);
  return (
    <section className={`${styles.panel} ${styles.researchSummary}`} id="history-comparison" ref={sectionRef}>
      <PanelHeader title="历史对照" />
      <ValidationSummary snapshot={snapshot} />
      <ValidationTable rows={snapshot.validation} />
    </section>
  );
}

function HoverHelp() {
  const id = useId();
  const box = useRef<HTMLDivElement>(null);
  const [hint, setHint] = useState<HTMLElement | null>(null);
  const [position, setPosition] = useState({ left: 0, top: 0 });
  useEffect(() => {
    const root = box.current!.parentElement!;
    const find = (target: EventTarget | null) => target instanceof Element ? target.closest<HTMLElement>("[data-tooltip]") : null;
    let dismissTimer: ReturnType<typeof setTimeout>;
    const show = (event: Event) => {
      clearTimeout(dismissTimer);
      if (box.current?.contains(event.target as Node)) return;
      const target = find(event.target);
      setHint(target?.dataset.tooltip ? target : null);
    };
    const leave = (event: MouseEvent | FocusEvent) => {
      const next = event.relatedTarget;
      if (next instanceof Node && box.current?.contains(next)) return;
      if (find(next) !== find(event.target)) dismissTimer = setTimeout(() => setHint(null), 120);
    };
    const close = () => { clearTimeout(dismissTimer); setHint(null); };
    const key = (event: globalThis.KeyboardEvent) => { if (event.key === "Escape") close(); };
    const click = (event: MouseEvent) => {
      const target = find(event.target);
      if (target?.closest(`.${styles.metricHint}, .${styles.combinationMemberTrigger}`)) show(event);
      else close();
    };
    root.addEventListener("pointerover", show);
    root.addEventListener("pointerout", leave);
    root.addEventListener("focusin", show);
    root.addEventListener("focusout", leave);
    root.addEventListener("click", click);
    window.addEventListener("keydown", key);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => {
      clearTimeout(dismissTimer);
      root.removeEventListener("pointerover", show);
      root.removeEventListener("pointerout", leave);
      root.removeEventListener("focusin", show);
      root.removeEventListener("focusout", leave);
      root.removeEventListener("click", click);
      window.removeEventListener("keydown", key);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, []);
  useLayoutEffect(() => {
    if (!hint || !box.current) return;
    const anchor = hint.getBoundingClientRect(), tip = box.current.getBoundingClientRect();
    setPosition({
      left: Math.max(12, Math.min(anchor.left, window.innerWidth - tip.width - 12)),
      top: Math.max(12, anchor.top - tip.height - 8 >= 12 ? anchor.top - tip.height - 8 : Math.min(anchor.bottom + 8, window.innerHeight - tip.height - 12)),
    });
    const previous = hint.getAttribute("aria-describedby");
    hint.setAttribute("aria-describedby", [previous, id].filter(Boolean).join(" "));
    return () => { if (previous) hint.setAttribute("aria-describedby", previous); else hint.removeAttribute("aria-describedby"); };
  }, [hint, id]);
  return <div ref={box} id={id} role="tooltip" className={styles.hoverHelp} hidden={!hint} style={position} onPointerLeave={() => setHint(null)}>{hint?.dataset.tooltip}</div>;
}

function MetricLabel({ label, note }: { label: string; note: string }) {
  return <span className={styles.metricHint}><button type="button" data-tooltip={note}>{label}</button></span>;
}

function MetricGrid({ metrics }: { metrics: MetricCardData[] }) {
  return (
    <section className={styles.metricGrid} aria-label="核心指标">
      {metrics.map((metric) => (
        <div className={`${styles.metricCard} ${toneClasses[metric.tone]}`} key={metric.key}>
          <MetricLabel label={metric.label} note={metric.key === "net" ? "多头持仓 − 空头持仓；净变化为相邻交易日之差。" : metric.note} />
          <div className={styles.metricValue}>
            {metric.value}
            {metric.unit && <small>{metric.unit}</small>}
          </div>
          {metric.key === "net" && <div className={styles.metricNote}>{metric.note}</div>}
          {metric.status === "unavailable" && <span className={styles.unavailableMark}>待定义</span>}
        </div>
      ))}
    </section>
  );
}

function PanelHeader({ title, subtitle, action }: { title: string; subtitle?: string; action?: React.ReactNode }) {
  return (
    <div className={styles.panelHeader}>
      <div>
        <h3>{title}</h3>
        {subtitle && <p>{subtitle}</p>}
      </div>
      {action}
    </div>
  );
}

function CombinationMatrix({ combinations, rows, onProduct }: { combinations: CombinationRow[]; rows: DashboardSnapshot["combinationMatrix"]; onProduct: (code: string) => void }) {
  const [sort, setSort] = usePreference("seats.matrix.sort", "change");
  const sortedRows = useMemo(() => {
    const ranked = rows.map((row, index) => {
      let count = 0, change: number | null = null;
      for (const combination of combinations) {
        const signal = row.signals[combination.id];
        if (signal?.aligned) count++;
        if (signal && signal.missingCount === 0 && signal.netChange != null) change = Math.max(change ?? 0, Math.abs(signal.netChange));
      }
      return { row, count, change, index };
    });
    return sort === "original" ? ranked : ranked.sort((a, b) => (sort === "signals"
      ? b.count - a.count || (b.change ?? -1) - (a.change ?? -1)
      : (b.change ?? -1) - (a.change ?? -1) || b.count - a.count) || a.index - b.index);
  }, [rows, combinations, sort]);
  const sortDescription = sort === "original"
    ? "按数据源顺序。"
    : sort === "signals" ? "按全员同向组数排序，同数按净变化幅度。" : "按完整组的最大净变化绝对值排序。";
  const tableScroll = useRef<HTMLDivElement>(null);
  return (
    <section className={styles.panel}>
      <PanelHeader
        title="席位分组"
        action={<label className={styles.matrixSortControl}>行排序<select className={styles.matrixSort} aria-label="组合矩阵排序" data-tooltip={sortDescription} value={sort} onChange={(event) => {
          setSort(event.target.value);
          tableScroll.current?.scrollTo({ top: 0 });
        }}><option value="change">净变化幅度：大 → 小</option><option value="signals">全员同向组数：多 → 少</option><option value="original">数据源顺序</option></select></label>}
      />
      <div ref={tableScroll} className={styles.matrixTableWrap} tabIndex={0} aria-label="席位分组表，可横向及纵向滚动">
        <table className={`${styles.matrixTable} ${styles.matrixTableTransposed}`}>
          <thead>
            <tr>
              <th scope="col"><span data-tooltip="首行左侧为净持仓，右侧为净变化，单位手。红底净多、绿底净空，深一档表示全员同向；缺席组用中性底色。">品种</span>{sort !== "original" && <span className={styles.matrixSortMetric}>{sort === "signals" ? "同向组 ↓" : "变化幅度 ↓"}</span>}</th>
              {combinations.map((combination) => (
                <th scope="col" key={combination.id}>
                  <button
                    type="button"
                    className={styles.combinationMemberTrigger}
                    data-combination-id={combination.id}
                    aria-label={`查看${combination.name}成员`}
                    data-tooltip={combination.members.join("、")}
                  >
                    <span className={styles.matrixColumnTitle}>{combination.name}</span>
                  </button>
                  <div className={styles.tableSecondary}>{combination.memberCount} 席</div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sortedRows.map(({ row, count, change }) => (
              <tr key={row.productCode}>
                <th scope="row"><button className={styles.seatButton} type="button" onClick={() => onProduct(row.productCode)} aria-label={`查看${row.label}席位`}>{row.label}</button><span>{row.root}</span>{sort !== "original" && <span className={styles.matrixSortMetric} data-tooltip={`全员同向 ${count}/${combinations.length} 组；完整组最大净变化 ${change == null ? "不可用" : `${formatNumber(change)} 手`}`}>{sort === "signals" ? `${count}/${combinations.length} 组` : change != null ? `${formatCompact(change)} 手` : "—"}</span>}</th>
                {combinations.map((combination) => {
                  const signal = row.signals[combination.id];
                  if (!signal) return <td key={combination.id}><span className={styles.matrixEmpty}>未统计</span></td>;
                  const net = signal.netPosition, change = signal.netChange;
                  return (
                    <td key={combination.id}>
                      <div className={`${styles.matrixSignal} ${!signal.missingCount && net ? net > 0 ? styles.matrixLong : styles.matrixShort : ""} ${signal.aligned ? styles.matrixAligned : ""}`}>
                        <span className={styles.matrixAmounts}>
                          <strong className={net != null && net > 0 ? styles.changePositive : net != null && net < 0 ? styles.changeNegative : undefined}>{net == null ? "—" : net === 0 ? "0" : `${net > 0 ? "+" : "−"}${formatNumber(Math.abs(net))}`}</strong>
                          {net != null && <b className={change != null && change > 0 ? styles.changePositive : change != null && change < 0 ? styles.changeNegative : undefined}>{change == null ? "—" : change === 0 ? "0" : formatSigned(change)}</b>}
                        </span>
                        <span className={styles.matrixMembers}>{net != null && <>多{signal.longCount} · 空{signal.shortCount}{signal.flatCount > 0 && ` · 平${signal.flatCount}`}</>}{signal.missingCount > 0 && <em>缺{signal.missingCount}</em>}</span>
                      </div>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function NetPositionPanel({ rows, onSeat, onTag }: { rows: NetPositionRow[]; onSeat: (seatId: string) => void; onTag: (tag: SeatTag) => void }) {
  return (
    <section className={styles.panel}>
      <PanelHeader title="净持仓 Top10" />
      <div className={styles.netList}>
        <div className={`${styles.netRow} ${styles.netHeader}`}><span>#</span><span>席位</span><span /><span>净持仓</span><span>日变动</span><span>特点</span></div>
        {rows.map((row) => (
          <div className={styles.netRow} key={row.seatId}>
            <span className={styles.rankNumber}>{String(row.rank).padStart(2, "0")}</span>
            <button className={styles.seatButton} type="button" onClick={() => onSeat(row.seatId)}>{row.seatName}</button>
            <span className={`${styles.directionMark} ${row.direction === "多" ? styles.changePositive : styles.changeNegative}`}>{row.direction}</span>
            <span className={styles.netValue}>{formatCompact(row.netPosition)}</span>
            <span className={`${styles.changeValue} ${row.change >= 0 ? styles.changePositive : styles.changeNegative}`}>{formatSigned(row.change)}</span>
            <div className={styles.rowTags}>{row.tags.slice(0, 1).map((tagItem) => <TagChip key={tagItem.name} tag={tagItem} compact onClick={onTag} />)}</div>
          </div>
        ))}
      </div>
    </section>
  );
}

function SignalRibbon({ counts }: { counts: DashboardSnapshot["overview"]["signalCounts"] }) {
  return (
    <section className={styles.signalRibbon} aria-label="所选交易日信号计数" style={{ "--signal-count": counts.length } as CSSProperties}>
      <h3 className={styles.signalRibbonIntro}>异动概览</h3>
      {counts.map((item) => (
        <div className={styles.signalCount} key={item.label}>
          <span className={`${styles.signalCountDot} ${toneClasses[item.tone]}`} aria-hidden="true" />
          <span>{item.label}</span>
          <strong>{item.value}</strong>
        </div>
      ))}
    </section>
  );
}

function SeatRankingPanel({
  title,
  rows,
  onSeat,
  onTag,
}: {
  title: string;
  rows: SeatRankingRow[];
  onSeat: (seatId: string) => void;
  onTag: (tag: SeatTag) => void;
}) {
  return (
    <section className={styles.panel}>
      <PanelHeader title={title} />
      <div className={styles.rankingScroll} tabIndex={0} aria-label={`${title}可滚动列表`}>
        <table className={styles.rankingTable}>
          <thead><tr><th>#</th><th>席位</th><th data-tooltip="披露持仓，单位：手">持仓</th><th data-tooltip="较上一交易日增减，单位：手">日变动</th><th data-tooltip="该席位持仓 ÷ 同方向披露总持仓">占比</th><th>特点</th></tr></thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.seatId}>
                <td><span className={styles.tableRank}>{String(row.rank).padStart(2, "0")}</span></td>
                <td><button className={styles.seatButton} type="button" onClick={() => onSeat(row.seatId)}>{row.seatName}</button></td>
                <td><strong>{formatCompact(row.position)}</strong></td>
                <td className={row.change >= 0 ? styles.changePositive : styles.changeNegative}>{formatSigned(row.change)}</td>
                <td>{formatPercent(row.share)}</td>
                <td><div className={styles.rowTags}>{row.tags.slice(0, 2).map((tagItem) => <TagChip key={tagItem.name} tag={tagItem} compact onClick={onTag} />)}{row.signal && !row.tags.some((tag) => tag.name === row.signal) && <span className={styles.signalLabel}>{row.signal}</span>}</div></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function TagChip({ tag, compact = false, onClick }: { tag: SeatTag; compact?: boolean; onClick: (tag: SeatTag) => void }) {
  const label = `${tag.source} · 标注评分 ${formatConfidence(tag.confidence)}`;
  return (
    <button className={`${styles.tagChip} ${compact ? styles.tagChipCompact : ""} ${toneClasses[tag.tone]}`} type="button" data-tooltip={label} aria-label={tag.name} onClick={() => onClick(tag)}>
      {tag.name}
    </button>
  );
}

function AnomalyTable({ rows, scope, onSeat, onTag }: { rows: AnomalyRow[]; scope: Scope; onSeat: (seatId: string, productCode?: string) => void; onTag: (tag: SeatTag) => void }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [severity, setSeverity] = usePreference("seats.anomaly.severity", "all");
  const filteredRows = useMemo(() => severity === "all" ? rows : rows.filter((row) => row.severity === severity), [rows, severity]);
  useEffect(() => { if (scrollRef.current) scrollRef.current.scrollTop = 0; }, [rows, severity]);
  return (
    <section className={styles.panel}>
      <PanelHeader title="席位异动" action={<div className={styles.tableControls}><span className={styles.rangeLabel}>范围：{scopeLabels[scope]}</span><select aria-label="筛选异动严重度" value={severity} onChange={(event) => setSeverity(event.target.value as typeof severity)}><option value="all">全部严重度</option><option value="high">高</option><option value="medium">中</option><option value="low">低</option></select></div>} />
      <div className={styles.anomalyTableWrap} ref={scrollRef} tabIndex={0} aria-label="席位异动可滚动列表">
        <table className={styles.anomalyTable}>
          <thead><tr><th>类型</th><th>严重度</th><th>席位</th><th>品种</th><th>详情</th><th>指标</th><th>时间</th></tr></thead>
          <tbody>
            {filteredRows.length === 0 ? <tr><td colSpan={7}><InlineEmpty label="当前筛选下暂无异动" /></td></tr> : filteredRows.map((row) => (
              <tr key={row.id}>
                <td><span className={styles.anomalyType}>{row.type}</span></td>
                <td><SeverityBadge severity={row.severity} /></td>
                <td><button className={styles.seatButton} type="button" onClick={() => onSeat(row.seatId, row.productCode)}>{row.seatName}</button></td>
                <td>{row.productCode}</td>
                <td><span className={styles.anomalyDetail}>{row.detail}</span></td>
                <td className={row.change >= 0 ? styles.changePositive : styles.changeNegative}>{row.metric}</td>
                <td><div className={styles.timeCell}>{row.occurredAt}<div className={styles.rowTags}>{row.tags.slice(0, 1).map((tagItem) => <TagChip key={tagItem.name} tag={tagItem} compact onClick={onTag} />)}</div></div></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className={styles.panelFootnote}>共 {filteredRows.length} 条异动</div>
    </section>
  );
}

function SeverityBadge({ severity }: { severity: AnomalyRow["severity"] }) {
  const labels = { high: "高", medium: "中", low: "低" };
  return <span className={`${styles.severityBadge} ${severity === "high" ? styles.severityHigh : severity === "medium" ? styles.severityMedium : styles.severityLow}`}><span aria-hidden="true">●</span>{labels[severity]}</span>;
}

function InlineEmpty({ label }: { label: string }) {
  return <span className={styles.inlineEmpty}><span aria-hidden="true">○</span>{label}</span>;
}

function MarketView({ snapshot, scope, onSeat, onTag, onProduct }: { snapshot: DashboardSnapshot; scope: Scope; onSeat: (seatId: string, productCode?: string) => void; onTag: (tag: SeatTag) => void; onProduct: (productCode: string) => void }) {
  return (
    <div className={styles.pageStack}>
      <SignalRibbon counts={snapshot.overview.signalCounts} />
      <AnomalyTable rows={snapshot.anomalies} scope={scope} onSeat={onSeat} onTag={onTag} />
      <MarketTreemap items={snapshot.treemap} onProduct={onProduct} />
    </div>
  );
}

function MarketTreemap({ items, onProduct }: { items: DashboardSnapshot["treemap"]; onProduct: (productCode: string) => void }) {
  const hasInterest = items.some((item) => item.openInterest != null);
  const [metric, setMetric] = usePreference("seats.market.metric", hasInterest ? "interest" : "seat");
  const interestMode = metric === "interest";
  const valueOf = (item: DashboardSnapshot["treemap"][number]) => interestMode ? item.openInterestChangeRate ?? null : item.change;
  const globalMagnitudes = items.flatMap((item) => {
    const value = valueOf(item);
    return value == null ? [] : [Math.abs(value)];
  }).sort((a, b) => a - b);
  const globalIntensity = new Map(globalMagnitudes.map((value, index) => [value, (index + 1) / globalMagnitudes.length]));
  const grouped = Object.groupBy(items, (item) => item.group);
  const groups = [...Object.keys(marketGroups), "未分类"].filter((group) => grouped[group]?.length);
  return (
    <section className={styles.panel}>
      <PanelHeader title="品种持仓变动" action={<select className={styles.matrixSort} aria-label="品种持仓变动指标" data-tooltip="仅统计有席位数据品种的真实合约。上市、到期或缺数会影响比较；持仓变化不等于资金流。" value={metric} onChange={(event) => setMetric(event.target.value)}><option value="interest" disabled={!hasInterest}>增持比例{hasInterest ? "" : "（暂无数据）"}</option><option value="seat">净持仓变动</option></select>} />
      <div className={styles.marketGroups} aria-label="按品种属性分组的品种持仓变动">
        {groups.map((group) => (
          <section key={group}>
            <div className={styles.marketGroupTitle}><strong>{group}</strong><span>{grouped[group]!.length} 个品种</span></div>
            <div className={styles.treemap}>
              {[...grouped[group]!].sort((a, b) => {
                const x = valueOf(a), y = valueOf(b);
                return x == null ? (y == null ? 0 : 1) : y == null ? -1 : interestMode ? y - x : Math.abs(y) - Math.abs(x);
              }).map((item) => {
                const value = valueOf(item);
                return (
                <button
                  className={`${styles.treeTile} ${value != null && value > 0 ? styles.treeLong : value != null && value < 0 ? styles.treeShort : styles.treeFlat}`}
                  key={`${item.productCode}-${item.label}`}
                  type="button"
                  onClick={() => onProduct(item.productCode)}
                  style={{ "--intensity": value ? globalIntensity.get(Math.abs(value)) ?? 0 : 0 } as CSSProperties}
                  data-tooltip={interestMode ? `${item.productCode} · 当前持仓 ${item.openInterest == null ? "缺失" : formatNumber(item.openInterest)} 手 / 前值 ${item.previousOpenInterest == null ? "缺失" : formatNumber(item.previousOpenInterest)} 手 · 对比日 ${item.previousInterestDate ?? "缺失"} · 合约数 ${item.interestContractCount ?? "—"}/${item.previousInterestContractCount ?? "—"}${item.interestContractsChanged ? " · 合约范围有变动" : ""}` : `${item.productCode} · 净持仓变化 ${formatNumber(item.change)} 手`}
                  aria-label={`${item.label}，${interestMode ? "增持比例" : "净持仓变动"} ${value == null ? "不可用" : interestMode ? `${(value * 100).toFixed(2)}%` : `${formatNumber(value)} 手`}，点击查看`}
                >
                  <strong>{item.label}</strong>
                  <span>{value == null ? "—" : interestMode ? `${value > 0 ? "+" : ""}${(value * 100).toFixed(2)}%` : formatSigned(value)}{!interestMode && <small> 手</small>}</span>
                  {interestMode && <small>{value == null ? "缺少可比数据" : `持仓 ${formatCompact(item.openInterest ?? 0)} 手`}{item.interestContractsChanged ? " · 范围变动" : ""}</small>}
                </button>
              ); })}
            </div>
          </section>
        ))}
      </div>
      <div className={styles.treeLegend}><span><i className={styles.legendLong} />{interestMode ? "总持仓增加" : "净持仓增加"}</span><span><i className={styles.legendShort} />{interestMode ? "总持仓减少" : "净持仓减少"}</span><span><i className={styles.legendFlat} />不变或不可比</span><span data-tooltip="按全市场变化绝对值着色，幅度越大越深。">色深：全市场排名</span></div>
    </section>
  );
}

function TermView({ snapshot, onSeat, onTag }: { snapshot: DashboardSnapshot; onSeat: (seatId: string) => void; onTag: (tag: SeatTag) => void }) {
  return (
    <div className={`${styles.pageStack} ${styles.productSection}`}>
      <div className={styles.productOverview}>
        <PanelHeader title={`${snapshot.meta.selectedProductLabel}概览`} />
        {snapshot.meta.selectedProductLabel !== "全市场" && <MetricGrid metrics={snapshot.overview.metrics} />}
      </div>
      <ResearchSummary snapshot={snapshot} />
      {snapshot.meta.selectedProductLabel !== "全市场" ? <TermChart contracts={snapshot.termStructure.contracts} productLabel={snapshot.termStructure.productLabel} /> : <InlineEmpty label="选择单一品种查看合约结构，避免混合不同合约单位" />}
      <OppositeSeatPanel rows={snapshot.termStructure.oppositeSeats} onSeat={onSeat} onTag={onTag} threshold={snapshot.termStructure.threshold} />
      <TermChangePanel rows={snapshot.termStructure.topChanges} onSeat={onSeat} onTag={onTag} />
    </div>
  );
}

function TermChart({ contracts, productLabel }: { contracts: TermContractRow[]; productLabel: string }) {
  const total = sumSignedValues(contracts.map((contract) => contract.netPosition));
  const prices = contracts.flatMap((contract) => contract.closePrice == null ? [] : [contract.closePrice]);
  const formatPrice = (price: number) => price.toLocaleString("zh-CN", { maximumFractionDigits: 3 });
  const priceMin = prices.length ? Math.min(...prices) : 0;
  const priceMax = prices.length ? Math.max(...prices) : 0;
  const pricePadding = Math.max((priceMax - priceMin) * 0.1, priceMax * 0.001, 0.01);
  const priceLow = priceMin - pricePadding, priceHigh = priceMax + pricePadding;
  const priceY = (price: number) => 154 - (price - priceLow) / (priceHigh - priceLow) * 142;
  const pricePath = contracts.map((contract, index) => contract.closePrice == null ? "" : `${index && contracts[index - 1].closePrice != null ? "L" : "M"}${(index + 0.5) / contracts.length * 1000},${priceY(contract.closePrice)}`).join(" ");
  const maxNetPosition = contracts.reduce((max, item) => Math.max(max, Math.abs(item.netPosition)), 1);
  return (
    <section className={`${styles.panel} ${styles.termChartPanel}`}>
      <PanelHeader title={`${productLabel}合约结构`} />
      <div className={styles.termChartScroll} tabIndex={0} aria-label="合约持仓与价格图，窄屏可横向滚动"><div className={styles.termChart} style={{ minWidth: `${Math.max(450, contracts.length * 90 + 140)}px` }}>
        <div className={styles.termAxis} aria-label="净持仓左轴，手"><span>+{formatCompact(maxNetPosition)}</span><span>0</span><span>−{formatCompact(maxNetPosition)}</span></div>
        {prices.length > 0 && <div className={`${styles.termAxis} ${styles.termPriceAxis}`} aria-label="收盘价右轴"><span>{formatPrice(priceHigh)}</span><span>{formatPrice((priceHigh + priceLow) / 2)}</span><span>{formatPrice(priceLow)}</span></div>}
        <div className={styles.termColumns} style={{ gridTemplateColumns: `repeat(${Math.max(1, contracts.length)}, minmax(0, 1fr))` }}>
          {prices.length > 0 && <svg className={styles.termPriceLine} viewBox="0 0 1000 166" preserveAspectRatio="none" role="img" aria-label="合约收盘价曲线，缺失价格处断开">
            <path d={pricePath} fill="none" stroke="var(--accent)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
            {contracts.map((contract, index) => contract.closePrice == null ? null : <circle key={contract.contractCode} cx={(index + 0.5) / contracts.length * 1000} cy={priceY(contract.closePrice)} r="3" fill="var(--accent)"><title>{contract.label} · {contract.priceDate} 收盘价 {formatPrice(contract.closePrice)}</title></circle>)}
          </svg>}
          {contracts.map((contract) => (
            <div className={`${styles.termColumn} ${contract.isNear ? styles.termColumnActive : ""}`} key={contract.contractCode}>
              <div className={styles.termBarArea}>
                <span className={styles.termZeroLine} aria-hidden="true" />
                <div className={`${styles.termBar} ${contract.netPosition > 0 ? styles.termBarPositive : contract.netPosition < 0 ? styles.termBarNegative : styles.termBarZero}`} style={{ "--bar-size": `${(Math.abs(contract.netPosition) / maxNetPosition) * 46}%` } as CSSProperties}>
                  <span>{formatSigned(contract.netPosition)}</span>
                </div>
              </div>
              <strong>{contract.label}</strong>
              <em className={contract.netPosition > 0 ? styles.changePositive : contract.netPosition < 0 ? styles.changeNegative : undefined}>{contract.netPosition > 0 ? "净多" : contract.netPosition < 0 ? "净空" : "持平"}</em>
              <span className={styles.termPriceLabel} data-tooltip={contract.closePrice == null ? "当日合约收盘价缺失" : `${contract.priceDate} · ${contract.contractCode} 收盘价`}>收 {contract.closePrice == null ? "—" : formatPrice(contract.closePrice)}</span>
            </div>
          ))}
        </div>
      </div></div>
      <div className={styles.termTotals} aria-label="展示合约合计"><strong data-tooltip={`展示 ${contracts.length} 个合约的净持仓按正负加总；净额 = 净多腿 − 净空腿。`}>合计</strong><span>净多腿 {formatNumber(total.positive)}</span><span>净空腿 {formatNumber(total.negative)}</span><span>净额 <b>{formatNumber(total.net)}</b> 手</span></div>
    </section>
  );
}

function OppositeSeatPanel({ rows, threshold, onSeat, onTag }: { rows: DashboardSnapshot["termStructure"]["oppositeSeats"]; threshold: number; onSeat: (seatId: string) => void; onTag: (tag: SeatTag) => void }) {
  const totalsByProduct = Object.entries(Object.groupBy(rows, (row) => row.productCode ?? "当前品种"));
  return (
    <section className={styles.panel}>
      <PanelHeader title="席位跨期持仓" subtitle={`双腿 ≥ ${threshold} 手 · 含其他非零合约`} action={<span className={styles.panelBadge}>{rows.length} 条席位记录</span>} />
      <div className={styles.oppositeList}>
        {rows.map((row) => {
          const net = row.legs?.length ? row.legs.reduce((sum, leg) => sum + leg.netPosition, 0) : null;
          return (
          <div className={styles.oppositeRow} key={`${row.seatId}-${row.productCode ?? "all"}-${row.nearContractCode ?? "near"}-${row.farContractCode ?? "far"}`}>
            <div className={styles.oppositeSeatCell}>
              <button className={styles.seatButton} type="button" onClick={() => onSeat(row.seatId)}>{row.seatName}</button>
              <span>{row.productCode}</span>
            </div>
            <div className={styles.oppositeDirection} data-tooltip="近、远按到期顺序；多、空指净持仓方向。不据此认定套利或套保。"><span className={row.nearDirection === "多" ? styles.changePositive : styles.changeNegative}>{row.nearDirection}近 <small>{row.nearContractCode?.split(".")[0]}</small></span><span className={row.farDirection === "多" ? styles.changePositive : styles.changeNegative}>{row.farDirection}远 <small>{row.farContractCode?.split(".")[0]}</small></span></div>
            <div className={styles.oppositeLegs}>{(row.legs ?? []).map((leg) => <span key={leg.contractCode}><b>{leg.label}</b><em className={leg.netPosition > 0 ? styles.changePositive : leg.netPosition < 0 ? styles.changeNegative : undefined}>{leg.netPosition > 0 ? "+" : ""}{formatNumber(leg.netPosition)}</em></span>)}<span className={styles.oppositeTotal}>展示腿净额合计 <b className={net != null && net > 0 ? styles.changePositive : net != null && net < 0 ? styles.changeNegative : undefined}>{net == null ? "—" : `${net > 0 ? "+" : ""}${formatNumber(net)}`}</b> 手</span></div>
            <div className={styles.rowTags}>{row.tags.map((tagItem) => <TagChip key={tagItem.name} tag={tagItem} compact onClick={onTag} />)}</div>
          </div>
        ); })}
      </div>
      {totalsByProduct.map(([product, productRows = []]) => {
        const total = sumSignedValues(productRows.flatMap((row) => (row.legs ?? []).map((leg) => leg.netPosition)));
        return <div className={styles.termTotals} aria-label={`${product}展示席位合计`} key={product}><strong data-tooltip={`${product} · 本榜 ${productRows.length} 条记录（含滚动区）的净合约腿；不与上图重复相加。`}>合计</strong><span>净多腿 <span className={total.positive > 0 ? styles.changePositive : undefined}>{formatNumber(total.positive)}</span></span><span>净空腿 <span className={total.negative > 0 ? styles.changeNegative : undefined}>{formatNumber(total.negative)}</span></span><span>净额 <b className={total.net > 0 ? styles.changePositive : total.net < 0 ? styles.changeNegative : undefined}>{total.net > 0 ? "+" : ""}{formatNumber(total.net)}</b> 手</span></div>;
      })}
    </section>
  );
}

function TermChangePanel({ rows, onSeat, onTag }: { rows: TermChangeRow[]; onSeat: (seatId: string) => void; onTag: (tag: SeatTag) => void }) {
  const maxChange = rows.reduce((max, row) => Math.max(max, Math.abs(row.netChange)), 1);
  const total = sumSignedValues(rows.map((row) => row.netChange));
  return (
    <section className={styles.panel}>
      <PanelHeader title="净持仓变化 Top10" />
      <div className={styles.termChangeList}>
        {rows.map((row) => (
          <div className={`${styles.termChangeRow} ${row.isReversal ? styles.termReversal : ""}`} key={row.seatId}>
            <span className={styles.rankNumber}>{String(row.rank).padStart(2, "0")}</span>
            <button className={styles.seatButton} type="button" onClick={() => onSeat(row.seatId)}>{row.seatName}</button>
            <div className={styles.termChangeBarTrack}><span className={row.netChange >= 0 ? styles.termChangeBarPositive : styles.termChangeBarNegative} style={{ width: `${Math.abs(row.netChange) / maxChange * 50}%` }} /></div>
            <strong className={row.netChange >= 0 ? styles.changePositive : styles.changeNegative}>{formatSigned(row.netChange)}</strong>
            {row.entryState === "new" || row.entryState === "exit" ? <span className={styles.reversalLabel} data-tooltip="缺席日按 0 估算，不等于实际开平仓。">{row.entryState === "new" ? "新入榜" : "退出榜"}</span> : row.isReversal ? <span className={styles.reversalLabel}>反转</span> : <span className={styles.reversalPlaceholder}>—</span>}
            <div className={styles.rowTags}>{row.tags.slice(0, 1).map((tagItem) => <TagChip key={tagItem.name} tag={tagItem} compact onClick={onTag} />)}</div>
          </div>
        ))}
      </div>
      <div className={styles.termTotals} aria-label="展示席位净变化合计"><strong data-tooltip={`本榜 ${rows.length} 个席位；缺席日按 0 估算。跨品种手数不代表资金规模。`}>合计</strong><span>净增 {formatNumber(total.positive)}</span><span>净减 {formatNumber(total.negative)}</span><span>变化净额 <b>{formatNumber(total.net)}</b> 手</span></div>
    </section>
  );
}

function hasHistoryResult(row: ValidationRuleRow): boolean {
  return row.sampleSize > 0 && row.returnValue != null && row.winRate != null;
}

function orderHistoryRows(rows: ValidationRuleRow[]): ValidationRuleRow[] {
  const days = (row: ValidationRuleRow) => row.horizon === "次日" ? 1 : Number.parseInt(row.horizon, 10);
  return [...rows].sort((a, b) => days(a) - days(b) || Number(hasHistoryResult(b)) - Number(hasHistoryResult(a)));
}

function missingHistoryReason(row: ValidationRuleRow): string {
  if (row.band === "middle" && row.sampleSize === 0) return "仅对照高、低档，中档不分组。";
  if (row.percentile == null) return "历史分位缺失。";
  return row.sampleSize ? "该项历史结果缺失。" : "无同档且后续价格完整的样本。";
}

function ValidationSummary({ snapshot }: { snapshot: DashboardSnapshot }) {
  const withResults = snapshot.validation.filter(hasHistoryResult);
  return (
    <section className={styles.validationSummary}>
      {snapshot.meta.selectedProductLabel !== "全市场" && [
        { key: "f2", label: "净持仓比率分位", value: snapshot.researchSummary.f2Percentile },
        { key: "net-cr5", label: "前五占比分位", value: snapshot.researchSummary.netCr5Percentile },
      ].map(({ key, label, value }) => {
        const metric = snapshot.overview.metrics.find((item) => item.key === key);
        return <div key={key}>
          <MetricLabel label={label} note={`${metric?.label ?? label}：当前 ${metric?.value ?? "—"}${metric?.unit ?? ""}。近 504 个有效观测中的位置：≥70% 高档，≤30% 低档；非上涨概率。`} />
          <strong>{value?.toFixed(1) ?? "—"}<small>%</small></strong>
        </div>;
      })}
      <div><MetricLabel label="对照覆盖" note="结果完整的行数 ÷ 全部对照行数。" /><strong>{withResults.length}<small> / {snapshot.validation.length}</small></strong></div>
      <div className={styles.validationMethod}><MetricLabel label="样本截止" note="与观察日隔离 25 个有效观测；后续价格不晚于截止日。" /><strong>{snapshot.researchSummary.libraryCutoff ?? "—"}</strong></div>
    </section>
  );
}

function ValidationTable({ rows }: { rows: ValidationRuleRow[] }) {
  const orderedRows = useMemo(() => orderHistoryRows(rows), [rows]);
  const multipleProducts = new Set(rows.map((row) => row.productCode)).size > 1;
  return (
    <section className={styles.panel}>
      <div className={styles.validationTableWrap} tabIndex={0} aria-label="历史对照表，可滚动">
        <table className={styles.validationTable}>
          <thead><tr><th>因子</th><th data-tooltip="样本日后第 N 个有效行情观测的累计涨跌；缺数可能拉长窗口。">后续窗口</th><th data-tooltip="同档样本累计收益的均值：窗口末收盘价 ÷ 样本日收盘价 − 1。">平均涨跌</th><th data-tooltip="同档样本累计涨跌幅的中位数。">中位涨跌</th><th data-tooltip="同档样本累计涨幅 > 0 的比例，非策略胜率。">上涨比例</th><th data-tooltip="后续价格完整的观测数；窗口可能重叠，非独立交易数。">样本</th></tr></thead>
          <tbody>
            {rows.length === 0 ? <tr><td colSpan={6}><InlineEmpty label="暂无历史对照数据" /></td></tr> : orderedRows.map((row) => (
              <tr key={row.id}>
                <td><strong>{row.factor}</strong>{multipleProducts && <div className={styles.tableSecondary}>{row.productCode ?? "—"}</div>}<div className={styles.tableSecondary} data-tooltip="当前因子的历史分位，不是上涨概率">{row.percentile == null ? "分位缺失" : `分位 ${row.percentile.toFixed(1)}%`}{row.band === "high" ? " · 高档" : row.band === "low" ? " · 低档" : row.band === "middle" ? " · 中档" : ""}</div></td>
                <td>{row.horizon === "次日" ? "后1日" : `后${row.horizon}`}</td>
                {[row.returnValue, row.medianReturn].map((value, index) => <td key={index} data-tooltip={value == null ? missingHistoryReason(row) : undefined} className={value == null || value === 0 ? undefined : value > 0 ? styles.changePositive : styles.changeNegative}>{value == null ? "—" : `${value > 0 ? "+" : value < 0 ? "−" : ""}${Math.abs(value).toFixed(2)}%`}</td>)}
                <td data-tooltip={row.winRate == null ? missingHistoryReason(row) : undefined}>{row.winRate == null ? "—" : formatPercent(row.winRate)}</td>
                <td data-tooltip={!row.sampleSize ? missingHistoryReason(row) : row.sampleSize < 60 ? "有效样本不足 60 个。" : undefined}>{row.sampleSize ? formatNumber(row.sampleSize) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className={styles.historyMethod}><MetricLabel label="统计口径" note="同档最多 1,260 个样本，中档不对照。按主力收盘价计算，未计交易成本及换月影响；未作基准比较或样本外验证。" /></div>
    </section>
  );
}

function TagInspector({ tag, onClose }: { tag: SeatTag; onClose: () => void }) {
  const definition = tag.name === "近远月反向" ? "同一席位、同一品种的不同到期合约出现相反净持仓，按发布批次的最小单腿阈值识别。它描述持仓结构，不确认交易意图。"
    : tag.name === "资金席位" ? "当前为参考图恢复的人工画像，没有自动量化识别公式；不能认定为主力资金，也不表示该席位全部持仓都在做方向交易。"
    : tag.name === "套保盘" ? "当前为参考图恢复的人工画像，未核验客户现货敞口或套保申报；不能确认该席位持仓正在进行套期保值。" : null;
  return (
    <aside className={styles.inspector} role="dialog" aria-modal="false" aria-label={`${tag.name}标签详情`}>
      <div className={styles.inspectorHeader}><div><span className={`${styles.tagChip} ${toneClasses[tag.tone]}`}>{tag.name}</span></div><button className={styles.closeButton} type="button" aria-label="关闭标签详情" onClick={onClose}>×</button></div>
      <h2>{tag.name}</h2>
      {definition && <p>{definition}</p>}
      <p>{tag.evidence}</p>
      <dl className={styles.inspectorList}><div><dt>来源</dt><dd>{tag.source}</dd></div><div><dt>标注评分</dt><dd>{formatConfidence(tag.confidence)}</dd></div><div><dt>有效期</dt><dd>{tag.effectiveFrom} 至 {tag.effectiveTo}</dd></div></dl>
      <div className={styles.inspectorNote}>评分是规则或人工标注，未经概率校准；标签不是席位身份或交易动机的认定。</div>
    </aside>
  );
}

function HistoryDrawer({ history, loading, onClose }: { history: SeatHistory | null; loading: boolean; onClose: () => void }) {
  const drawer = useRef<HTMLElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    drawer.current?.querySelector("button")?.focus();
    return () => { document.body.style.overflow = overflow; previous?.focus(); };
  }, []);
  const chartPoints = history?.points.slice(-60) ?? [];
  const maxNetPosition = chartPoints.reduce((max, point) => Math.max(max, Math.abs(point.netPosition)), 1);
  return (
    <div className={styles.drawerBackdrop} role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <aside ref={drawer} className={styles.historyDrawer} role="dialog" aria-modal="true" aria-label="席位历史详情" onKeyDown={(event) => {
        if (event.key === "Escape") onClose();
        if (event.key === "Tab") {
          const controls = drawer.current?.querySelectorAll<HTMLElement>('button, [tabindex="0"]');
          const first = controls?.[0], last = controls?.[controls.length - 1];
          if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
          if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
        }
      }}>
        <div className={styles.drawerHeader}><h2>{history?.seatName ?? "席位历史"}</h2><button className={styles.closeButton} type="button" aria-label="关闭历史详情" onClick={onClose}>×</button></div>
        {loading && <div className={styles.drawerLoading}><span className={styles.loadingSpinner} aria-hidden="true">◌</span>正在读取历史序列…</div>}
        {!loading && history && <>
          <div className={styles.drawerMeta}><span>{history.productLabel}</span><span>已加载 {history.points.length} 个交易日</span></div>
          {chartPoints.length ? <div className={styles.historyChart}>
            <span>近 {chartPoints.length} 个交易日净持仓</span>
            <svg viewBox="0 0 600 180" role="img" aria-label="席位净持仓历史曲线">
              <line x1="55" x2="595" y1="90" y2="90" stroke="var(--line-strong)" />
              <text x="0" y="24">{formatCompact(maxNetPosition)}</text><text x="0" y="94">0</text><text x="0" y="164">{formatCompact(-maxNetPosition)}</text>
              <polyline fill="none" stroke="var(--accent)" strokeWidth="2" points={chartPoints.map((point, index) => `${55 + index / Math.max(1, chartPoints.length - 1) * 535},${90 - point.netPosition / maxNetPosition * 70}`).join(" ")} />
            </svg>
            <div><span>{chartPoints[0].date}</span><span>{chartPoints.at(-1)?.date}</span></div>
          </div> : <InlineEmpty label="当前筛选下暂无历史记录" />}
          <div className={styles.historyTable} tabIndex={0} aria-label="历史持仓明细，可滚动"><div><span>日期</span><span>多头</span><span>空头</span><span>净持仓</span></div>{[...history.points].reverse().map((point) => <div key={point.date}><span>{point.date}</span><span>{formatCompact(point.longPosition)}</span><span>{formatCompact(point.shortPosition)}</span><strong className={point.netPosition >= 0 ? styles.changePositive : styles.changeNegative}>{formatSigned(point.netPosition)}</strong></div>)}</div>
        </>}
      </aside>
    </div>
  );
}

export default Dashboard;
