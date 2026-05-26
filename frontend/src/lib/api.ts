import axios from 'axios';
import type {
  ArticleListResponse,
  ArticleParams,
  DigestParams,
  DigestResponse,
  EdgarFacets,
  EdgarFiling,
  EdgarParams,
  FeedHealth,
  ForwardCurveData,
  MarketRow,
  MetricPoint,
  SofrPoint,
  StatusResponse,
} from './types';

const BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000/api';

export const api = axios.create({ baseURL: BASE });

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

// ── Deals ──────────────────────────────────────────────
export const getDeals = () => api.get('/deals');
export const getDealOverview = (id: string) => api.get(`/deals/${id}/overview`);
export const getDealReplines = (id: string) => api.get(`/deals/${id}/replines`);
export const getDealPerformance = (id: string) => api.get(`/deals/${id}/performance`);
export const getDealCovenants = (id: string) => api.get(`/deals/${id}/covenants`);

// ── System ─────────────────────────────────────────────
export const getStatus = () => api.get<StatusResponse>('/status');
