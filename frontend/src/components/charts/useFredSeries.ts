// Fetch several FRED series and merge them into chart rows keyed by date.

import { useQueries } from '@tanstack/react-query';
import { getFredHistory } from '../../lib/api';
import { qk } from '../../lib/queryKeys';

export interface FredSeriesDef {
  seriesId: string;
  key: string; // dataKey used in the chart row
  name: string;
  color: string;
  // If set, plot year-over-year % change instead of the level. Value is the
  // number of observations that make up one year (monthly=12, quarterly=4,
  // daily≈252, weekly=52).
  yoyPeriods?: number;
}

// One chart row: a date plus one numeric value per series dataKey.
export type FredChartRow = { date: string } & Record<string, unknown>;

interface Result {
  rows: FredChartRow[];
  isLoading: boolean;
  isError: boolean;
}

export function useFredSeries(series: FredSeriesDef[], limit = 240): Result {
  const results = useQueries({
    queries: series.map((s) => ({
      queryKey: qk.fredHistory(s.seriesId),
      queryFn: () => getFredHistory(s.seriesId, limit).then((r) => r.data),
      staleTime: 30 * 60_000,
    })),
  });

  const isLoading = results.some((r) => r.isLoading);
  const isError = results.some((r) => r.isError);

  // Merge by date: { date, [key]: value, ... }. Optionally transform to YoY %.
  const byDate = new Map<string, FredChartRow>();
  results.forEach((res, i) => {
    const def = series[i];
    const pts = res.data ?? []; // ascending by date
    if (def.yoyPeriods && def.yoyPeriods > 0) {
      const p = def.yoyPeriods;
      for (let j = p; j < pts.length; j++) {
        const cur = pts[j].value;
        const prior = pts[j - p].value;
        if (cur != null && prior != null && prior !== 0) {
          const row = byDate.get(pts[j].date) ?? { date: pts[j].date };
          row[def.key] = (cur / prior - 1) * 100;
          byDate.set(pts[j].date, row);
        }
      }
    } else {
      for (const pt of pts) {
        const row = byDate.get(pt.date) ?? { date: pt.date };
        row[def.key] = pt.value;
        byDate.set(pt.date, row);
      }
    }
  });

  const rows = [...byDate.values()].sort((a, b) =>
    String(a.date).localeCompare(String(b.date)),
  );

  return { rows, isLoading, isError };
}
