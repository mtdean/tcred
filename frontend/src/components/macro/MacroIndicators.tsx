// Headline macro indicators: latest / prev / YoY change / updated.

import { useQueries } from '@tanstack/react-query';
import { getFredHistory } from '../../lib/api';
import { qk } from '../../lib/queryKeys';
import type { MetricPoint } from '../../lib/types';
import { fmtMonth, signClass } from '../../lib/utils';
import Panel from '../shared/Panel';
import LoadingCursor from '../shared/LoadingCursor';

type Mode = 'rate' | 'yoy' | 'index';

interface IndicatorDef {
  seriesId: string;
  label: string;
  mode: Mode;
  periods: number; // observations back for "year ago"
}

const INDICATORS: IndicatorDef[] = [
  { seriesId: 'UNRATE', label: 'Unemployment Rate', mode: 'rate', periods: 12 },
  { seriesId: 'CPIAUCSL', label: 'CPI (YoY)', mode: 'yoy', periods: 12 },
  { seriesId: 'CPILFESL', label: 'Core CPI (YoY)', mode: 'yoy', periods: 12 },
  { seriesId: 'GDPC1', label: 'Real GDP (YoY)', mode: 'yoy', periods: 4 },
  { seriesId: 'UMCSENT', label: 'U. Michigan Sentiment', mode: 'index', periods: 12 },
];

interface Computed {
  label: string;
  latest: string;
  prev: string;
  yoy: number | null; // change vs year ago (pp)
  yoyUnit: string;
  updated: string;
}

function compute(def: IndicatorDef, data: MetricPoint[]): Computed | null {
  const pts = data.filter((p) => p.value != null);
  if (pts.length < 2) return null;
  const n = pts.length;
  const last = pts[n - 1].value as number;
  const prev = pts[n - 2].value as number;
  const updated = fmtMonth(pts[n - 1].date);

  if (def.mode === 'yoy') {
    const yearAgo = pts[n - 1 - def.periods]?.value as number | undefined;
    const prevYearAgo = pts[n - 2 - def.periods]?.value as number | undefined;
    const latestYoY = yearAgo ? (last / yearAgo - 1) * 100 : null;
    const prevYoY = prevYearAgo ? (prev / prevYearAgo - 1) * 100 : null;
    return {
      label: def.label,
      latest: latestYoY != null ? `${latestYoY.toFixed(1)}%` : '—',
      prev: prevYoY != null ? `${prevYoY.toFixed(1)}%` : '—',
      yoy: latestYoY != null && prevYoY != null ? latestYoY - prevYoY : null,
      yoyUnit: 'pp',
      updated,
    };
  }

  // rate / index: show level; YoY is the level change vs a year ago.
  const yearAgo = pts[n - 1 - def.periods]?.value as number | undefined;
  const isRate = def.mode === 'rate';
  const fmtLevel = (v: number) => (isRate ? `${v.toFixed(1)}%` : v.toFixed(1));
  return {
    label: def.label,
    latest: fmtLevel(last),
    prev: fmtLevel(prev),
    yoy: yearAgo != null ? last - yearAgo : null,
    yoyUnit: isRate ? 'pp' : '',
    updated,
  };
}

export default function MacroIndicators() {
  const results = useQueries({
    queries: INDICATORS.map((d) => ({
      queryKey: qk.fredHistory(d.seriesId),
      queryFn: () => getFredHistory(d.seriesId, 60).then((r) => r.data),
      staleTime: 30 * 60_000,
    })),
  });

  const loading = results.some((r) => r.isLoading);
  const rows = INDICATORS.map((d, i) => compute(d, results[i].data ?? [])).filter(
    (r): r is Computed => r !== null,
  );

  return (
    <Panel title="Macro Indicators">
      {loading && rows.length === 0 ? (
        <LoadingCursor />
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Indicator</th>
              <th style={{ textAlign: 'right' }}>Latest</th>
              <th style={{ textAlign: 'right' }}>Prev</th>
              <th style={{ textAlign: 'right' }}>Δ YoY</th>
              <th style={{ textAlign: 'right' }}>Updated</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.label}>
                <td>{r.label}</td>
                <td className="num" style={{ color: 'var(--text-primary)' }}>{r.latest}</td>
                <td className="num muted">{r.prev}</td>
                <td className="num">
                  <span className={signClass(r.yoy)}>
                    {r.yoy == null ? '—' : `${r.yoy > 0 ? '+' : ''}${r.yoy.toFixed(1)}${r.yoyUnit}`}
                  </span>
                </td>
                <td className="num dim">{r.updated}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}
