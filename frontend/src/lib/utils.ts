// Formatting helpers — Bloomberg conventions: tabular figures, signed deltas.

import axios from 'axios';
import { format, formatDistanceToNowStrict, parseISO } from 'date-fns';

/** Human-readable message from an API error: FastAPI `detail`, then HTTP
 *  status, then Error.message, then the supplied fallback. */
export function apiErrorMessage(err: unknown, fallback: string): string {
  if (axios.isAxiosError(err)) {
    const detail = err.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (err.response?.status) return `Request failed (${err.response.status}).`;
  }
  if (err instanceof Error && err.message) return err.message;
  return fallback;
}

/** Signed percentage, e.g. +1.23% / -0.45%. Returns '—' for null. */
export function pct(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) return '—';
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(digits)}%`;
}

/** Unsigned percentage for levels (e.g. delinquency rate). */
export function pctLevel(value: number | null | undefined, digits = 1): string {
  if (value == null || Number.isNaN(value)) return '—';
  return `${value.toFixed(digits)}%`;
}

/** Currency with thousands separators. */
export function currency(value: number | null | undefined, digits = 0): string {
  if (value == null || Number.isNaN(value)) return '—';
  return value.toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/** Plain number with grouping. */
export function num(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) return '—';
  return value.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/** CSS class for a signed value: 'pos' | 'neg' | ''. */
export function signClass(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value) || value === 0) return '';
  return value > 0 ? 'pos' : 'neg';
}

/** Format an ISO timestamp as 'MMM d, HH:mm'. Tolerates null/garbage. */
export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  try {
    return format(parseISO(iso), 'MMM d, HH:mm');
  } catch {
    return iso;
  }
}

/** Format an ISO date as 'MMM d, yyyy'. */
export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  try {
    return format(parseISO(iso), 'MMM d, yyyy');
  } catch {
    return iso;
  }
}

/** Format an ISO date as 'MMM yyyy' (for monthly/quarterly series). */
export function fmtMonth(iso: string | null | undefined): string {
  if (!iso) return '—';
  try {
    return format(parseISO(iso), 'MMM yyyy');
  } catch {
    return iso;
  }
}

/** Relative time, e.g. '3h ago'. */
export function fmtRelative(iso: string | null | undefined): string {
  if (!iso) return '—';
  try {
    return `${formatDistanceToNowStrict(parseISO(iso))} ago`;
  } catch {
    return iso;
  }
}

/** Wall-clock HH:MM:SS for the top bar. */
export function clockString(d: Date = new Date()): string {
  return d.toLocaleTimeString('en-US', { hour12: false });
}

/** Keep only points within the trailing N years; null years → all points. */
export function sliceByYears<T extends { date: string }>(
  data: T[],
  years: number | null,
): T[] {
  if (years == null) return data;
  const cutoff = new Date();
  cutoff.setFullYear(cutoff.getFullYear() - years);
  const iso = cutoff.toISOString().slice(0, 10);
  return data.filter((d) => d.date >= iso);
}

/** Keep only points within the trailing N days; null → all points. */
export function sliceByDays<T extends { date: string }>(data: T[], days: number | null): T[] {
  if (days == null) return data;
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - days);
  const iso = cutoff.toISOString().slice(0, 10);
  return data.filter((d) => d.date >= iso);
}

/** Percentile rank (0-100) of `value` within `values` (share at or below). */
export function percentileRank(values: number[], value: number): number | null {
  const clean = values.filter((v) => v != null && !Number.isNaN(v));
  if (clean.length === 0) return null;
  const atOrBelow = clean.filter((v) => v <= value).length;
  return Math.round((atOrBelow / clean.length) * 100);
}

/** Ordinal suffix: 1→1st, 2→2nd, 67→67th. */
export function ordinal(n: number): string {
  const s = ['th', 'st', 'nd', 'rd'];
  const v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

// Series IDs we compute/ingest ourselves (no FRED page). Everything else stored
// in `metrics` is a genuine FRED series ID. HHDC_FLOW* (NY Fed) is prefix-matched.
const NON_FRED_SERIES = new Set([
  'RECESSION_RISK_ENSEMBLE', 'RECESSION_RISK_ENSEMBLE_EW',
  'NYFED_RECESSION_PROB', 'EBP_REC_PROB', 'NTFS_REC_PROB',
  'NEAR_TERM_FWD_SPREAD', 'EBP', 'GZ_SPREAD', 'CFSI', 'CREDIT_IMPULSE',
  'OFR_FSI', 'OFR_FSI_CREDIT', 'OFR_FSI_EQUITY', 'OFR_FSI_SAFE',
  'OFR_FSI_FUNDING', 'OFR_FSI_VOL', 'BIS_CREDIT_GAP_US',
]);

/** FRED series page URL, or null for our computed/ingested (non-FRED) series. */
export function fredSeriesUrl(seriesId: string | undefined): string | null {
  if (!seriesId || NON_FRED_SERIES.has(seriesId) || seriesId.startsWith('HHDC_FLOW')) {
    return null;
  }
  return `https://fred.stlouisfed.org/series/${seriesId}`;
}

// Computed/ingested series → anchor in docs/INDICATORS.md ("Calculated fields"
// reference). Anchors are the GitHub auto-anchors of that section's headings.
const DOC_BASE = 'https://github.com/mtdean/tcred/blob/main/docs/INDICATORS.md';
const DOC_ANCHORS: Record<string, string> = {
  RECESSION_RISK_ENSEMBLE: 'recession-risk-ensemble',
  RECESSION_RISK_ENSEMBLE_EW: 'recession-risk-ensemble',
  NYFED_RECESSION_PROB: 'yield-curve-recession-probit',
  EBP: 'excess-bond-premium',
  GZ_SPREAD: 'excess-bond-premium',
  EBP_REC_PROB: 'excess-bond-premium',
  NEAR_TERM_FWD_SPREAD: 'near-term-forward-spread',
  NTFS_REC_PROB: 'near-term-forward-spread',
  CREDIT_IMPULSE: 'credit-impulse',
  CFSI: 'consumer-financial-stress-index',
  OFR_FSI: 'ofr-financial-stress-index',
  OFR_FSI_CREDIT: 'ofr-financial-stress-index',
  OFR_FSI_EQUITY: 'ofr-financial-stress-index',
  OFR_FSI_SAFE: 'ofr-financial-stress-index',
  OFR_FSI_FUNDING: 'ofr-financial-stress-index',
  OFR_FSI_VOL: 'ofr-financial-stress-index',
  BIS_CREDIT_GAP_US: 'bis-credit-to-gdp-gap',
};

/** Methodology-doc URL (source/calculation/interpretation) for a computed series. */
export function seriesDocUrl(seriesId: string | undefined): string | null {
  if (!seriesId) return null;
  if (seriesId.startsWith('HHDC_FLOW')) return `${DOC_BASE}#delinquency-transition-flows`;
  const anchor = DOC_ANCHORS[seriesId];
  return anchor ? `${DOC_BASE}#${anchor}` : null;
}

/** Where a legend label should link: FRED for raw series, the methodology doc
 *  for computed ones. Returns null when neither applies. */
export function seriesReference(
  seriesId: string | undefined,
): { url: string; title: string } | null {
  const fred = fredSeriesUrl(seriesId);
  if (fred) return { url: fred, title: `${seriesId} — open on FRED` };
  const doc = seriesDocUrl(seriesId);
  if (doc) return { url: doc, title: `${seriesId} — data source, calculation & how to read it` };
  return null;
}
