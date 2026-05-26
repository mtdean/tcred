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
