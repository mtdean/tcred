// Formatting helpers — Bloomberg conventions: tabular figures, signed deltas.

import { format, formatDistanceToNowStrict, parseISO } from 'date-fns';

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
