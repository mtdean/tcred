import axios from 'axios';
import type {
  ArticleListResponse,
  ArticleParams,
  DigestParams,
  DigestResponse,
  EdgarFacets,
  EdgarFiling,
  EdgarParams,
  AbsDeal,
  AbsDealSummaryRow,
  AbsMomentumDelta,
  AbsNewIssueListResponse,
  AbsRatingBucket,
  AbsSpreadMetric,
  AbsSpreadSeriesResponse,
  BdcAggregateTrendPoint,
  BdcNonaccrualHolding,
  BdcNonaccrualTrendPoint,
  BdcSummaryRow,
  BdcWatchEntry,
  CloSpreadProxyPoint,
  CreditImpulsePoint,
  FeedHealth,
  ForwardCurveData,
  H8SeriesMetrics,
  KbraPresale,
  MarketRow,
  MetricPoint,
  RegulatoryAction,
  SofrPoint,
  StatusResponse,
} from './types';

const BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000/api';
const STATIC_MODE = import.meta.env.VITE_STATIC_MODE === 'true';
const SNAPSHOT_BASE = `${import.meta.env.BASE_URL || '/'}api-snapshot`;

export const STATIC_MODE_ENABLED = STATIC_MODE;
export class StaticModeError extends Error {
  constructor() {
    super('not available');
    this.name = 'StaticModeError';
  }
}

// Size/window params we strip when building the snapshot key. We snapshot
// each endpoint once at its widest setting and let the chart clip — so a
// request for days_back=180 happily reads from the days_back=1095 file.
const KEY_IGNORED_PARAMS = new Set(['limit', 'offset', 'days_back', 'hours_back']);

function snapshotKey(path: string, params: Record<string, unknown> = {}): string {
  const cleanPath = path.replace(/^\/+|\/+$/g, '').replace(/\//g, '__');
  const kept = Object.entries(params)
    .filter(([k, v]) => !KEY_IGNORED_PARAMS.has(k) && v !== undefined && v !== null && v !== '')
    .map(([k, v]) => [k, String(v)] as [string, string])
    .sort(([a], [b]) => a.localeCompare(b));
  const suffix = kept.length ? '__' + kept.map(([k, v]) => `${k}-${v}`).join('__') : '';
  return cleanPath + suffix + '.json';
}

export const api = axios.create({ baseURL: BASE });

if (STATIC_MODE) {
  api.interceptors.request.use(async (config) => {
    const method = (config.method || 'get').toLowerCase();
    if (method !== 'get') {
      throw new StaticModeError();
    }
    const path = (config.url || '').replace(/^\/+/, '');
    const params = (config.params || {}) as Record<string, unknown>;
    const file = snapshotKey(path, params);
    // Rewrite to fetch the snapshot from the static asset folder. We also
    // clear params so axios doesn't append a query string to the .json URL.
    config.baseURL = SNAPSHOT_BASE;
    config.url = '/' + file;
    config.params = undefined;
    return config;
  });
}

// ── Articles ───────────────────────────────────────────
export const getArticles = (params: ArticleParams) =>
  api.get<ArticleListResponse>('/articles', { params });
export const getFeedHealth = () => api.get<FeedHealth[]>('/articles/feed-health');
export const markRead = (id: string) => api.post(`/articles/${id}/read`);
export const triggerRefresh = () => api.post('/articles/refresh');
export const generateDigest = (body: DigestParams) =>
  api.post<DigestResponse>('/digest', body);
export const getDigests = (limit = 60) =>
  api.get<DigestResponse[]>('/digests', { params: { limit } });

// ── Market ─────────────────────────────────────────────
export const getMarketSnapshot = () => api.get<MarketRow[]>('/market/snapshot');
export const getMarketHistory = (ticker: string, limit = 252) =>
  api.get<MetricPoint[]>(`/market/history/${ticker}`, { params: { limit } });
export const getPercentiles = () => api.get('/market/percentiles');

// ── FRED ───────────────────────────────────────────────
export const getFredLatest = () => api.get('/fred/latest');
export const getFredHistory = (seriesId: string, limit = 260) =>
  api.get<MetricPoint[]>(`/fred/history/${seriesId}`, { params: { limit } });
export const getForwardCurve = () => api.get<ForwardCurveData>('/fred/forward-curve');
export const getSofrRates = (limit = 300) =>
  api.get<SofrPoint[]>('/fred/sofr', { params: { limit } });

// ── EDGAR ──────────────────────────────────────────────
export const getEdgarFilings = (params: EdgarParams = {}) =>
  api.get<EdgarFiling[]>('/edgar/filings', { params });
export const getEdgarFacets = () => api.get<EdgarFacets>('/edgar/facets');
export const getAbsPricing = (segment?: string) =>
  api.get<AbsDeal[]>('/abs/pricing', { params: { segment: segment || undefined } });
export const getAbsMomentumDeltas = () =>
  api.get<AbsMomentumDelta[]>('/abs/spread-momentum/deltas');

// ── 424B5 new-issue parser ─────────────────────────────────
export const getAbsNewIssues = (params: {
  asset_class?: string;
  days_back?: number;
  min_confidence?: 'low' | 'medium' | 'high';
  limit?: number;
} = {}) => api.get<AbsNewIssueListResponse>('/abs/new-issues', { params });

export const getAbsSpreadSeries = (params: {
  asset_class: string;
  rating_bucket?: AbsRatingBucket;
  metric?: AbsSpreadMetric;
  days_back?: number;
}) => api.get<AbsSpreadSeriesResponse>('/abs/spread-series', { params });

export const getAbsDealSummary = (days_back = 90) =>
  api.get<AbsDealSummaryRow[]>('/abs/deal-summary', { params: { days_back } });

export const triggerAbsNewIssuesRefresh = (days_back = 14) =>
  api.post<{ tranches_stored: number }>('/abs/new-issues/refresh', null, {
    params: { days_back },
  });

export const triggerAbsPricingRefresh = (days_back = 30) =>
  api.post<{ tranches: number }>('/abs/pricing/refresh', null, {
    params: { days_back },
  });

// ── Deals ──────────────────────────────────────────────
export const getDeals = () => api.get('/deals');
export const getDealOverview = (id: string) => api.get(`/deals/${id}/overview`);
export const getDealReplines = (id: string) => api.get(`/deals/${id}/replines`);
export const getDealPerformance = (id: string) => api.get(`/deals/${id}/performance`);
export const getDealCovenants = (id: string) => api.get(`/deals/${id}/covenants`);

// ── Phase 7: BDC Portfolio Monitor ─────────────────────
export const getBdcWatchList = () =>
  api.get<BdcWatchEntry[]>('/bdc/watch-list');
export const getBdcNonaccrualTrend = () =>
  api.get<BdcNonaccrualTrendPoint[]>('/bdc/nonaccrual-trend');
export const getBdcSummary = (period?: string) =>
  api.get<BdcSummaryRow[]>('/bdc/summary', { params: period ? { period } : undefined });
export const getBdcLatestPerBdc = () =>
  api.get<BdcSummaryRow[]>('/bdc/latest-per-bdc');
export const getBdcAggregateTrend = () =>
  api.get<BdcAggregateTrendPoint[]>('/bdc/aggregate-trend');
export const getBdcNonaccruals = (limit = 100) =>
  api.get<BdcNonaccrualHolding[]>('/bdc/nonaccruals', { params: { limit } });
export const triggerBdcRefresh = () =>
  api.post<{ holdings_stored: number }>('/bdc/refresh');

// ── Phase 7: Regulatory Flow Monitor ───────────────────
export const getRegulatoryActions = (params: {
  agency?: string;
  action_type?: string;
  min_score?: number;
  days_back?: number;
  limit?: number;
} = {}) => api.get<RegulatoryAction[]>('/regulatory/actions', { params });
export const triggerRegulatoryRefresh = (days_back = 7) =>
  api.post<{ actions_stored: number }>('/regulatory/refresh', null, {
    params: { days_back },
  });
export const triggerRegulatoryScore = (limit = 100) =>
  api.post<{ actions_scored: number }>('/regulatory/score', null, {
    params: { limit },
  });

// ── Phase 7: Fed H.8 Bank Credit Monitor ───────────────
export const getH8Metrics = () => api.get<H8SeriesMetrics[]>('/h8/metrics');
export const getCreditImpulse = () =>
  api.get<CreditImpulsePoint[]>('/h8/credit-impulse');

// ── Phase 7: CLO Stress Monitor ────────────────────────
export const getCloSpreadProxy = () =>
  api.get<CloSpreadProxyPoint[]>('/clo/spread-proxy');
export const getCloFilings = (limit = 50) =>
  api.get<EdgarFiling[]>('/clo/filings', { params: { limit } });

// ── Phase 7: KBRA Presale Parser ───────────────────────
export const getKbraPresales = (params: { asset_class?: string; limit?: number } = {}) =>
  api.get<KbraPresale[]>('/kbra/presales', { params });
export const triggerKbraRefresh = () =>
  api.post<{ presales_processed: number }>('/kbra/refresh');

// ── System ─────────────────────────────────────────────
export const getStatus = () => api.get<StatusResponse>('/status');
