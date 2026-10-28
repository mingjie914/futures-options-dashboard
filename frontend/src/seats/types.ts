
export type DashboardView =
  | "combination"
  | "market"
  | "term";

export type DataState =
  | "loading"
  | "ready"
  | "empty"
  | "partial"
  | "stale"
  | "error";

export type Scope = "all" | "main" | "retail";

export type Tone =
  | "accent"
  | "gold"
  | "positive"
  | "negative"
  | "warning"
  | "neutral";

export type SeatTagName =
  | "资金席位"
  | "套保盘"
  | "弱势席位"
  | "重点跟踪"
  | "疑似套利"
  | "多空反转"
  | "持仓异动"
  | "近远月反向";

export interface DashboardFilters {
  productCode: string;
  tradeDate: string;
  scope: Scope;
}

export interface ProductOption {
  code: string;
  label: string;
  exchange: string;
}

export interface SnapshotMeta {
  state: Exclude<DataState, "loading" | "error">;
  statusLabel: string;
  tradeDate: string;
  updatedAt: string;
  selectedProductLabel: string;
  warnings: string[];
}

export interface SeatTag {
  name: SeatTagName;
  tone: Tone;
  source: string;
  confidence: number;
  effectiveFrom: string;
  effectiveTo: string;
  evidence: string;
}

export interface MetricCardData {
  key: string;
  label: string;
  value: string;
  unit?: string;
  note: string;
  tone: Tone;
  status?: "ready" | "unavailable";
}

export interface DivergenceCardData {
  code: string;
  label: string;
  count: number;
  tone: Tone;
  products: Array<{ code: string; label: string }>;
}

export interface OverviewData {
  hasSeatGroups: boolean;
  metrics: MetricCardData[];
  signalCounts: Array<{ label: string; value: number; tone: Tone }>;
}

export interface CombinationRow {
  id: string;
  name: string;
  scope: string;
  memberCount: number;
  members: string[];
}

export interface CombinationPositionCell {
  combinationId: string;
  netPosition: number | null;
  netChange: number | null;
  longCount: number;
  shortCount: number;
  flatCount: number;
  missingCount: number;
  aligned: boolean;
}

export interface CombinationProductRow {
  productCode: string;
  root: string;
  label: string;
  exchange: string;
  signals: Record<string, CombinationPositionCell>;
}

export interface ResearchSummaryData {
  f2Percentile: number | null;
  netCr5Percentile: number | null;
  libraryCutoff: string | null;
}

export interface NetPositionRow {
  rank: number;
  seatId: string;
  seatName: string;
  direction: "多" | "空";
  netPosition: number;
  change: number;
  tags: SeatTag[];
}

export interface SeatRankingRow {
  rank: number;
  seatId: string;
  seatName: string;
  position: number;
  change: number;
  share: number;
  signal?: string;
  tags: SeatTag[];
}

export type AnomalySeverity = "high" | "medium" | "low";

export interface AnomalyRow {
  id: string;
  type: string;
  severity: AnomalySeverity;
  seatId: string;
  seatName: string;
  productCode: string;
  detail: string;
  metric: string;
  change: number;
  tags: SeatTag[];
  occurredAt: string;
}

export interface TreemapItem {
  group: string;
  productCode: string;
  label: string;
  change: number;
  openInterestChangeRate?: number | null;
  openInterest?: number | null;
  previousOpenInterest?: number | null;
  previousInterestDate?: string | null;
  interestContractCount?: number | null;
  previousInterestContractCount?: number | null;
  interestContractsChanged?: boolean;
}

export interface TermContractRow {
  contractCode: string;
  label: string;
  netPosition: number;
  isNear: boolean;
  closePrice?: number | null;
  priceDate?: string | null;
}

export interface OppositeSeatRow {
  seatId: string;
  seatName: string;
  productCode?: string;
  nearContractCode?: string;
  farContractCode?: string;
  nearDirection: "多" | "空";
  farDirection: "多" | "空";
  legs?: Array<{
    contractCode: string;
    label: string;
    netPosition: number;
  }>;
  tags: SeatTag[];
}

export interface TermChangeRow {
  rank: number;
  seatId: string;
  seatName: string;
  netChange: number;
  entryState?: string;
  isReversal: boolean;
  tags: SeatTag[];
}

export interface TermStructureData {
  productLabel: string;
  threshold: number;
  contracts: TermContractRow[];
  oppositeSeats: OppositeSeatRow[];
  topChanges: TermChangeRow[];
}

export interface ValidationRuleRow {
  id: string;
  productCode?: string;
  medianReturn?: number | null;
  factor: string;
  horizon: string;
  percentile: number | null;
  returnValue: number | null;
  winRate: number | null;
  sampleSize: number;
  band?: string;
}

export interface HistoryPoint {
  date: string;
  longPosition: number;
  shortPosition: number;
  netPosition: number;
}

export interface SeatHistory {
  seatId: string;
  seatName: string;
  productLabel: string;
  points: HistoryPoint[];
}

export interface DashboardSnapshot {
  meta: SnapshotMeta;
  products: ProductOption[];
  overview: OverviewData;
  divergenceCards: DivergenceCardData[];
  divergencePair?: string;
  combinations: CombinationRow[];
  combinationMatrix: CombinationProductRow[];
  researchSummary: ResearchSummaryData;
  netTop: NetPositionRow[];
  longTop: SeatRankingRow[];
  shortTop: SeatRankingRow[];
  anomalies: AnomalyRow[];
  treemap: TreemapItem[];
  termStructure: TermStructureData;
  validation: ValidationRuleRow[];
}
