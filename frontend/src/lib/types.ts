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
}

export type SourceType = 'news' | 'letter';

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

export interface ArticleListResponse {
  items: Article[];
  offset: number;
  limit: number;
}

export interface ArticleParams {
  min_score?: number;
  category?: string;
  source_type?: SourceType;
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

export interface StatusResponse {
  articles: { total: number; scored: number };
  metrics: number;
  edgar_filings: number;
  feeds: { live: number; total: number };
  last_news_refresh: string | null;
  last_abs_pricing_refresh: string | null;
  last_abs_424b5_refresh: string | null;
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

export type AbsRatingBucket = 'AAA' | 'AA' | 'A' | 'BBB' | 'BB_and_below';
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

export interface AbsSpreadSeriesResponse {
  asset_class: string;
  rating_bucket: AbsRatingBucket;
  metric: AbsSpreadMetric;
  series: AbsSpreadSeriesPoint[];
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
}

export interface EdgarParams {
  limit?: number;
  offset?: number;
  form_type?: string;
  asset_class?: string;
}

export interface EdgarFacets {
  form_types: string[];
  asset_classes: string[];
}
