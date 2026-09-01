import axios from 'axios';
import type {
  Article,
  ArticleContent,
  ArticleListResponse,
  ArticleParams,
  ArticleSearchResponse,
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
  MacroForecasts,
  MacroViews,
  KbraPresale,
  MarketRow,
  MetricPoint,
  RegulatoryAction,
  SofrPoint,
  StatusResponse,
  TrustPerformanceLatest,
  TrustPerformanceRow,
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

/** Build an ordered list of snapshot file names to try in static mode,
 *  starting with the exact combination, then each single-axis fallback,
 *  finally the no-filter snapshot. Stops the gh-pages build from silently
 *  404ing whenever the user picks a 2-axis filter combo we didn't pre-bake. */
function snapshotFallbackKeys(
  path: string,
  params: Record<string, unknown>,
): string[] {
  const filtered: [string, unknown][] = Object.entries(params)
    .filter(([k, v]) =>
      !KEY_IGNORED_PARAMS.has(k) && v !== undefined && v !== null && v !== '');
  const keys = new Set<string>();
  keys.add(snapshotKey(path, params));
  // Each single-axis variant (one filter at a time).
  for (const [k, v] of filtered) {
    keys.add(snapshotKey(path, { [k]: v }));
  }
  // No-filter (size/window-only params).
  keys.add(snapshotKey(path, {}));
  return [...keys];
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
    const candidates = snapshotFallbackKeys(path, params);

    // HEAD-check each candidate in order; serve the first that exists.
    // This lets multi-axis filter combos fall back to single-axis ones,
    // then the no-filter snapshot — which matches the design comment in
    // snapshot_api.py and stops gh-pages silently returning empty.
    let chosen = candidates[candidates.length - 1];
    for (const k of candidates) {
      try {
        const r = await fetch(`${SNAPSHOT_BASE}/${k}`, { method: 'HEAD' });
        if (r.ok) {
          chosen = k;
          break;
        }
      } catch {
        // network blip — try the next candidate
      }
    }
    config.baseURL = SNAPSHOT_BASE;
    config.url = '/' + chosen;
    config.params = undefined;
    return config;
  });
}

// ── Articles ───────────────────────────────────────────
export const getArticles = (params: ArticleParams) =>
  api.get<ArticleListResponse>('/articles', { params });
export const getFeedHealth = () => api.get<FeedHealth[]>('/articles/feed-health');
export const getArticleContent = (id: string) =>
  api.get<ArticleContent>(`/articles/${id}/content`);
export const searchArticles = (q: string, daysBack = 365, limit = 50) =>
  api.get<ArticleSearchResponse>('/articles/search', {
    params: { q, days_back: daysBack, limit },
  });
export const markRead = (id: string) => api.post(`/articles/${id}/read`);
export const triggerRefresh = () => api.post('/articles/refresh');
export const triggerMarketRefresh = () => api.post<{ rows: number }>('/market/refresh');
export const triggerFredRefresh = () =>
  api.post<{ fred_rows: number; indicator_rows: number }>('/fred/refresh');
export const triggerEdgarRefresh = () =>
  api.post<{ inserted: number }>('/edgar/refresh');
export const generateDigest = (body: DigestParams) =>
  api.post<DigestResponse>('/digest', body);
export const getDigests = (limit = 60) =>
  api.get<DigestResponse[]>('/digests', { params: { limit } });

// ── Analyst Briefings ──────────────────────────────────
export interface BriefingListItem {
  id: string;
  period_label: string;
  generated_at: string;
  model: string;
  preview: string;
  input_tokens: number | null;
  output_tokens: number | null;
}

export interface BriefingWatchItem {
  title: string;
  severity: 'info' | 'watch' | 'warn';
  why: string;
}

export interface Briefing {
  id: string;
  period_label: string;
  generated_at: string;
  model: string;
  briefing_md: string;
  watch_items: BriefingWatchItem[] | null;
  snapshot?: Record<string, unknown>;
  usage: {
    input_tokens: number | null;
    output_tokens: number | null;
    cache_read_input_tokens: number | null;
    cache_creation_input_tokens: number | null;
  };
}

export interface BriefingChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface BriefingChatResponse {
  reply: string;
  tool_calls: { name: string; input: Record<string, unknown> }[];
  usage: {
    input_tokens: number;
    output_tokens: number;
    cache_read_input_tokens: number;
    cache_creation_input_tokens: number;
  };
}

// ── ABS issuance (SIFMA) ───────────────────────────────
export interface SifmaSeries {
  label: string;
  points: { date: string; value: number }[];
}

export interface AbsIssuanceResponse {
  sifma: Record<string, SifmaSeries>;
  fred_supplement: {
    series_id: string;
    label: string;
    points: { date: string; value: number }[];
  };
  last_ingest: string | null;
  drop_dir: string;
}

export interface SifmaRefreshResult {
  files: number;
  records: number;
  rows_written: number;
  fred_supplement_rows?: number;
  drop_dir?: string;
}

export const getAbsIssuance = () =>
  api.get<AbsIssuanceResponse>('/abs/issuance');
export const triggerAbsIssuanceRefresh = () =>
  api.post<SifmaRefreshResult>('/abs/issuance/refresh');

// ── Issuer / deal pivot ────────────────────────────────
export interface IssuerListItem {
  issuer_name: string;
  deal_count: number;
  latest_filing_date: string | null;
}

export interface IssuerDealTranche {
  class_name: string | null;
  principal_amount: number | null;
  coupon_type: string | null;
  coupon_rate: number | null;
  floating_index: string | null;
  floating_spread_bps: number | null;
  wal_years: number | null;
  spread_to_benchmark: number | null;
  benchmark: string | null;
  rating_sp: string | null;
  rating_moodys: string | null;
  rating_kbra: string | null;
}

export interface IssuerDeal {
  accession_no: string;
  issuer_name: string | null;
  depositor: string | null;
  filing_date: string | null;
  closing_date: string | null;
  asset_class: string | null;
  total_deal_size: number | null;
  edgar_url: string | null;
  parse_confidence: string | null;
  n_tranches: number;
  senior_class_name: string | null;
  senior_spread_bps: number | null;
  senior_wal_years: number | null;
  tranches: IssuerDealTranche[];
}

export interface IssuerSummary {
  query: string;
  as_of: string;
  stats: {
    n_deals: number;
    total_volume: number;
    earliest_filing: string | null;
    latest_filing: string | null;
    n_asset_classes: number;
  };
  deals: IssuerDeal[];
  pricing: Record<string, unknown>[];
  edgar_filings: EdgarFiling[];
  kbra_presales: Record<string, unknown>[];
  articles: Article[];
}

export const listIssuers = (limit = 100) =>
  api.get<{ items: IssuerListItem[] }>('/issuers', { params: { limit } });

export const getIssuerSummary = (q: string) =>
  api.get<IssuerSummary>('/issuers/summary', { params: { q } });

// ── Watchlists ─────────────────────────────────────────
export interface Watchlist {
  id: string;
  name: string;
  description: string | null;
  keywords: string[];
  news_categories: string[] | null;
  edgar_asset_classes: string[] | null;
  edgar_form_types: string[] | null;
  regulatory_agencies: string[] | null;
  min_score: number;
  created_at: string;
  updated_at: string;
  last_viewed_at: string | null;
}

export interface WatchlistResults {
  watchlist: Watchlist;
  as_of: string;
  matches: {
    articles: Article[];
    edgar_filings: EdgarFiling[];
    regulatory_actions: RegulatoryAction[];
  };
  counts: {
    articles: number;
    edgar_filings: number;
    regulatory_actions: number;
    total: number;
  };
}

export interface WatchlistCreate {
  name: string;
  description?: string | null;
  keywords: string[];
  news_categories?: string[] | null;
  edgar_asset_classes?: string[] | null;
  edgar_form_types?: string[] | null;
  regulatory_agencies?: string[] | null;
  min_score?: number;
}

export const listWatchlists = () =>
  api.get<{ items: Watchlist[] }>('/watchlists');
export const createWatchlist = (body: WatchlistCreate) =>
  api.post<Watchlist>('/watchlists', body);
export const updateWatchlist = (id: string, patch: Partial<WatchlistCreate>) =>
  api.patch<Watchlist>(`/watchlists/${id}`, patch);
export const deleteWatchlist = (id: string) =>
  api.delete<{ deleted: string }>(`/watchlists/${id}`);
export const getWatchlistResults = (id: string) =>
  api.get<WatchlistResults>(`/watchlists/${id}/results`);
export const markWatchlistViewed = (id: string) =>
  api.post<Watchlist>(`/watchlists/${id}/viewed`);

// ── Freshness ──────────────────────────────────────────
export type FreshnessStatus = 'fresh' | 'stale' | 'dead' | 'missing';

export interface FreshnessSeries {
  series_id: string;
  label: string | null;
  category: string | null;
  frequency: string;
  latest_date: string | null;
  days_since: number | null;
  status: FreshnessStatus;
}

export interface FreshnessJob {
  job_id: string;
  status: string | null;
  started_at: string | null;
  ended_at: string | null;
  duration_ms: number | null;
  rows_ingested: number | null;
  hours_since: number | null;
  error: string | null;
}

export interface FreshnessReport {
  as_of: string;
  series: FreshnessSeries[];
  summary: { fresh: number; stale: number; dead: number; missing: number };
  jobs: FreshnessJob[];
}

export const getFreshness = () => api.get<FreshnessReport>('/freshness');

// ── Percentile / regime context ───────────────────────
export interface PercentilePoint {
  series_id: string;
  value: number;
  as_of: string;
  window_days: number;
  n_obs: number;
  percentile: number;
  min: number;
  max: number;
  median: number;
}

export interface PercentilesResponse {
  window_days: number;
  series: Record<string, PercentilePoint>;
}

export const getSeriesPercentiles = (seriesIds: string[], windowDays = 1825) =>
  api.get<PercentilesResponse>('/percentiles', {
    params: { series_ids: seriesIds.join(','), window_days: windowDays },
  });

export const listBriefings = (limit = 30) =>
  api.get<{ items: BriefingListItem[] }>('/briefings', { params: { limit } });

export const getLatestBriefing = () =>
  api.get<Briefing>('/briefings/latest');

export const getBriefing = (id: string) =>
  api.get<Briefing>(`/briefings/${id}`);

export const generateBriefing = (period_label?: string) =>
  api.post<Briefing>('/briefings/generate', null, {
    params: period_label ? { period_label } : undefined,
  });

export const chatWithBriefing = (
  id: string,
  message: string,
  history: BriefingChatMessage[],
) =>
  api.post<BriefingChatResponse>(`/briefings/${id}/chat`, { message, history });

// ── Market ─────────────────────────────────────────────
export const getMarketSnapshot = () => api.get<MarketRow[]>('/market/snapshot');
export const getMarketHistory = (ticker: string, limit = 252) =>
  api.get<MetricPoint[]>(`/market/history/${ticker}`, { params: { limit } });

// ── FRED ───────────────────────────────────────────────
export const getFredLatest = () => api.get('/fred/latest');
export const getFredHistory = (seriesId: string, limit = 260) =>
  api.get<MetricPoint[]>(`/fred/history/${seriesId}`, { params: { limit } });
export const getForwardCurve = () => api.get<ForwardCurveData>('/fred/forward-curve');
export const getMacroForecasts = () => api.get<MacroForecasts>('/macro/forecasts');
export const getMacroViews = () => api.get<MacroViews>('/macro/forecasts/views');
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

// ── Master-trust monthly performance (10-D) ────────────────
export const getTrustPerformance = (metric?: string) =>
  api.get<TrustPerformanceRow[]>('/trust-performance', {
    params: { metric: metric || undefined },
  });

export const getTrustPerformanceLatest = () =>
  api.get<TrustPerformanceLatest[]>('/trust-performance/latest');

export const triggerTrustPerformanceRefresh = (days_back = 35) =>
  api.post<{ rows: number }>('/trust-performance/refresh', null, {
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
