// Shared API response & request types.

export interface Article {
  id: string;
  feed_name: string;
  feed_category: string;
  title: string;
  snippet: string | null;
  url: string;
  published_at: string | null;
  fetched_at: string;
  relevance_score: number | null;
  relevance_tags: string | null;
  is_read: number;
  source_type: SourceType;
  // Dedup metadata (populated when /api/articles excludes duplicates).
  cluster_id?: string | null;
  duplicate_of?: string | null;
  n_sources?: number;
  other_sources?: string[];
}

export type SourceType =
  | 'bloomberg'
  | 'wsj'
  | 'ft'
  | 'marketwatch'
  | 'cnbc'
  | 'nyt'
  | 'reuters'
  | 'research'      // economist / central-bank research-blog newsletters
  | 'restructuring' // distressed / restructuring credit letters
  | 'letter'
  | 'news'; // catch-all for aggregator feeds (e.g. Google News queries)

export interface AbsTranche {
  class_name: string;
  rating: string | null;
  wal: number | null;
  benchmark: string | null;
  spread_bps: number | null;
  coupon: number | null;
}

export interface AbsDeal {
  accession_no: string;
  deal_name: string;
  segment: string;
  pricing_date: string;
  url: string;
  tranches: AbsTranche[];
}

export type AbsSeniority = 'senior' | 'mezzanine' | 'junior';

// One observation in a segment+seniority spread-momentum series: the change in
// representative spread vs the prior comparable deal (widening +, tightening −).
export interface AbsMomentumDelta {
  segment: string;
  seniority: AbsSeniority;
  pricing_date: string;
  deal_name: string;
  accession_no: string;
  spread_bps: number;
  prior_spread_bps: number | null;
  delta_bps: number | null;
  zscore: number | null;
}

// One (trust, period, metric) observation from a 10-D distribution report.
export interface TrustPerformanceRow {
  cik: number;
  trust_name: string;
  segment: string;
  period_end: string;
  filed_at: string;
  metric: string;
  value: number;
  url: string;
}

export interface TrustPerformanceLatest {
  trust_name: string;
  cik: number;
  period_end: string;
  url: string;
  metrics: Record<string, number>;
}

export interface ArticleListResponse {
  items: Article[];
  offset: number;
  limit: number;
}

export interface ArticleParams {
  min_score?: number;
  category?: string;
  // Single slug or comma-separated list (e.g. 'wsj,ft,bloomberg').
  source_type?: string;
  limit?: number;
  offset?: number;
}

export interface DigestParams {
  hours_back?: number;
  min_score?: number;
}

export interface FeedHealth {
  feed_name: string;
  url: string;
  last_checked: string | null;
  is_live: number;
  http_status: number | null;
  error_msg: string | null;
  needs_url: number;
  platform: string | null;
}

// Latest run per scheduled job (job_runs table via /api/status `jobs`).
export interface JobRun {
  id: number;
  job_id: string;
  started_at: string;
  ended_at: string | null;
  status: 'running' | 'success' | 'error';
  duration_ms: number | null;
  rows_ingested: number | null;
  error: string | null;
  triggered_by: string;
}

export interface StatusResponse {
  articles: { total: number; scored: number };
  metrics: number;
  edgar_filings: number;
  feeds: { live: number; total: number };
  jobs: JobRun[];
  last_news_refresh: string | null;
  last_market_refresh: string | null;
  last_fred_refresh: string | null;
  last_abs_pricing_refresh: string | null;
  last_abs_424b5_refresh: string | null;
  last_bdc_refresh: string | null;
  last_regulatory_refresh: string | null;
  last_regulatory_score: string | null;
  last_kbra_refresh: string | null;
}

export interface MarketRow {
  ticker: string;
  label: string;
  category: string;
  price: number | null;
  date: string;
  chg_1d: number | null;
  chg_5d: number | null;
  chg_30d: number | null;
}

export interface MetricPoint {
  date: string;
  value: number | null;
}

// ── Macro forecasts (macrobot bundle) ─────────────────────────
export interface MacroForecastRow {
  series_id: string;
  label: string;
  date: string;
  value: number;
}

export interface MacroForecasts {
  macro_forecasts: MacroForecastRow[];
  macro_curve: MacroForecastRow[];
  macro_regime: MacroForecastRow[];
}

export interface MacroViewPathPoint {
  h_m: number;
  ensemble: number | null;
  lo: number | null;
  hi: number | null;
  members: Record<string, number | null>;
  weights: Record<string, number | null>;
}

export interface MacroViewConcept {
  key: string;
  label: string;
  unit: string;
  band_multiplier?: number | null;
  path: MacroViewPathPoint[];
}

export interface MacroViewForwardSegment {
  segment: string;
  start_m: number;
  end_m: number;
  ensemble: number | null;
  lo: number | null;
  hi: number | null;
  members: Record<string, number | null>;
}

export interface MacroViews {
  available: boolean;
  reason?: string;
  asof?: string;
  concepts?: MacroViewConcept[];
  forward_curve?: MacroViewForwardSegment[];
  regime?: {
    labels: Record<string, string | boolean>;
    evidence: Record<string, number | null>;
  };
  credit_view?: {
    stance: string;
    score: number | null;
    n_signals: number;
    rationale: string;
    components: {
      valuation: string;
      loss_cycle: string;
      volatility: string;
      funding: string;
    };
    carry_to_risk?: Record<string, number | null>;
    evidence: Record<string, number | null>;
    scenarios?: {
      name: string;
      stance: string;
      score: number | null;
      rationale: string;
      components: {
        valuation: string;
        loss_cycle: string;
        volatility: string;
        funding: string;
      };
    }[];
  };
}

export interface ForwardCurveData {
  tenors: string[];
  today: Record<string, number | null>;
  six_months_ago: Record<string, number | null>;
  one_year_ago: Record<string, number | null>;
  as_of: {
    today: string | null;
    six_months_ago: string | null;
    one_year_ago: string | null;
  };
}

export interface SofrPoint {
  date: string;
  m1: number | null;
  m3: number | null;
  y1: number | null;
}

export interface DigestResponse {
  date?: string; // YYYY-MM-DD in US/Eastern (present on saved digests)
  session?: 'AM' | 'PM'; // before noon ET / noon+ ET
  summary: string;
  article_count: number;
  hours_back: number;
  min_score: number;
  date_range: { from: string; to: string };
  model: string;
  generated_at: string;
}

// 424B5 new-issue parser (data/abs_parser.py). Richer schema than the FWP
// path: per-tranche ratings, CUSIPs, coupon type, and a treasury-derived
// spread. `parse_confidence` flags rows for the UI to dim or filter.
export interface AbsNewIssue {
  id: string;
  accession_no: string;
  edgar_url: string;
  filing_date: string;

  issuer_name: string | null;
  depositor: string | null;
  servicer: string | null;
  asset_class: string;
  closing_date: string | null;
  cutoff_date: string | null;
  total_deal_size: number | null;
  underwriters: string | null;
  parse_confidence: 'high' | 'medium' | 'low';

  class_name: string;
  principal_amount: number | null;
  coupon_type: 'fixed' | 'floating' | null;
  coupon_rate: number | null;
  floating_index: string | null;
  floating_spread_bps: number | null;
  wal_years: number | null;
  final_payment_date: string | null;
  legal_maturity_date: string | null;
  price_to_public: number | null;
  cusip: string | null;

  rating_sp: string | null;
  rating_moodys: string | null;
  rating_kbra: string | null;
  rating_fitch: string | null;

  benchmark: string | null;
  benchmark_rate: number | null;
  spread_to_benchmark: number | null;
  implied_yield: number | null;
}

export interface AbsNewIssueListResponse {
  items: AbsNewIssue[];
  count: number;
}

export type AbsRatingBucket = 'all' | 'AAA' | 'AA' | 'A' | 'BBB' | 'BB_and_below';
export type AbsSpreadMetric =
  | 'spread_to_benchmark'
  | 'implied_yield'
  | 'floating_spread_bps'
  | 'coupon_rate';

export interface AbsSpreadSeriesPoint {
  week: string;       // 'YYYY-WNN'
  week_start: string; // earliest filing_date in the week
  avg_spread: number | null;
  min_spread: number | null;
  max_spread: number | null;
  n_tranches: number;
}

// Rank of the latest weekly average vs the trailing context window
// (share of weeks at or below the latest value).
export interface AbsSpreadPercentile {
  latest: number;
  rank: number;
  window_days: number;
  n_weeks: number;
}

export interface AbsSpreadSeriesResponse {
  asset_class: string;
  rating_bucket: AbsRatingBucket;
  metric: AbsSpreadMetric;
  series: AbsSpreadSeriesPoint[];
  percentile: AbsSpreadPercentile | null;
}

export interface AbsDealSummaryRow {
  asset_class: string;
  deal_count: number;
  total_volume: number | null;
  avg_spread_bps: number | null;
  earliest: string | null;
  latest: string | null;
}

export interface EdgarFiling {
  accession_no: string;
  company_name: string | null;
  form_type: string | null;
  filed_at: string | null;
  description: string | null;
  url: string | null;
  asset_class: string | null;
  issuance_type: 'debt' | 'equity' | null;
}

export interface EdgarParams {
  limit?: number;
  offset?: number;
  form_type?: string;
  asset_class?: string;
  issuance_type?: 'debt' | 'equity';
}

export interface EdgarFacets {
  form_types: string[];
  asset_classes: string[];
  issuance_types: string[];
}

// ── Phase 7: BDC Portfolio Monitor ───────────────────────────────
export interface BdcWatchEntry {
  cik: string;
  ticker: string;
  name: string;
}

export interface BdcNonaccrualTrendPoint {
  period: string;
  n_bdcs: number;
  total_nonaccrual_fv: number | null;
  total_fv: number | null;
  avg_nonaccrual_rate: number | null;
  avg_mark_to_cost: number | null;
  avg_wa_rate: number | null;
}

export interface BdcSummaryRow {
  id: string;
  cik: string;
  bdc_name: string;
  period: string;
  adsh: string | null;
  filed: string | null;
  filing_url: string | null;
  total_fair_value: number | null;
  total_cost_basis: number | null;
  nonaccrual_fv: number | null;
  nonaccrual_cost: number | null;
  nonaccrual_rate_fv: number | null;
  nonaccrual_rate_cost: number | null;
  pct_first_lien: number | null;
  pct_second_lien: number | null;
  pct_equity: number | null;
  wa_interest_rate: number | null;
  mark_to_cost: number | null;
  n_holdings: number | null;
  fetched_at: string;
}

export interface BdcAggregateTrendPoint {
  period: string;
  n_bdcs: number;
  total_fv: number | null;
  total_cost: number | null;
  mark_to_cost: number | null;
  wa_interest_rate: number | null;
  pct_first_lien: number | null;
  pct_second_lien: number | null;
  pct_equity: number | null;
}

export interface BdcNonaccrualHolding {
  bdc_name: string;
  company_name: string | null;
  industry: string | null;
  investment_type: string | null;
  cost_basis: number | null;
  fair_value: number | null;
  period: string;
}

// ── Phase 7: Regulatory Flow Monitor ─────────────────────────────
export type RegulatoryAgency = 'CFPB' | 'OCC' | 'FDIC' | 'Fed' | 'SEC';
export type RegulatoryActionType =
  | 'RULE'
  | 'PROPOSED_RULE'
  | 'NOTICE'
  | 'PRESS_RELEASE';

export interface RegulatoryAction {
  id: string;
  agency: RegulatoryAgency | string;
  action_type: RegulatoryActionType | string;
  title: string;
  abstract: string | null;
  publication_date: string | null;
  effective_date: string | null;
  comment_close_date: string | null;
  document_number: string | null;
  docket_id: string | null;
  cfr_references: string | null;
  html_url: string | null;
  pdf_url: string | null;
  relevance_score: number | null;
  relevance_tags: string | null;
  fetched_at: string;
}

// ── Phase 7: Fed H.8 Bank Credit Monitor ─────────────────────────
export interface H8MetricPoint {
  date: string;
  value: number | null;
  yoy_pct: number | null;
}

export interface H8SeriesMetrics {
  series_id: string;
  label: string;
  latest_date: string;
  latest_value: number | null;
  wow_change: number | null;
  wow_pct: number | null;
  yoy_pct: number | null;
  ma4w: number | null;
  history: H8MetricPoint[];
}

export interface CreditImpulsePoint {
  date: string;
  total_loans: number;
  qoq_change: number | null;
  credit_impulse: number | null;
}

// ── Phase 7: CLO Stress Monitor ──────────────────────────────────
export interface CloSpreadProxyPoint {
  date: string;
  aaa_series: string;
  mezz_series: string;
  aaa_price: number;
  mezz_price: number;
  price_ratio: number | null;
}

// ── Phase 7: KBRA Presale Parser ─────────────────────────────────
export interface KbraPresale {
  id: string;
  deal_name: string | null;
  issuer: string | null;
  asset_class: string | null;
  closing_date: string | null;
  pdf_filename: string | null;
  base_cdr: number | null;
  base_cpr: number | null;
  base_loss_rate: number | null;
  base_severity: number | null;
  ce_aaa: number | null;
  ce_aa: number | null;
  ce_a: number | null;
  ce_bbb: number | null;
  ce_bb: number | null;
  parse_confidence: 'high' | 'medium' | 'low' | null;
  parsed_at: string | null;
}
