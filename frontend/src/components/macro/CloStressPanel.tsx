// CLO Stress Monitor — two stacked panels:
//   1. CLO Spread Proxy: JBBB/JAAA price-ratio time series (stress when the
//      ratio falls — mezz selling off relative to AAA). Light secondary line
//      for the CLOA/JBBB pair as a corroborating read.
//   2. Recent CLO-tagged EDGAR filings, matched server-side via the
//      asset_class_keywords list (data_sources.yaml).
//
// Data path: GET /api/clo/spread-proxy + GET /api/clo/filings. No refresh
// button — both endpoints sit on top of the market and EDGAR schedulers.

import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { getCloSpreadProxy } from '../../lib/api';
import { qk } from '../../lib/queryKeys';
import type { CloSpreadProxyPoint } from '../../lib/types';
import { COLORS } from '../../lib/colors';
import TooltipShell from '../charts/TooltipShell';
import { fmtDate } from '../../lib/utils';
import Panel from '../shared/Panel';
import LoadingCursor from '../shared/LoadingCursor';
import EmptyState from '../shared/EmptyState';

const PRIMARY_PAIR = { aaa: 'mkt_JAAA', mezz: 'mkt_JBBB' } as const;
const SECONDARY_PAIR = { aaa: 'mkt_CLOA', mezz: 'mkt_JBBB' } as const;

// Pivot the long-form API rows into one row per date with one column per pair.
function toChartData(points: CloSpreadProxyPoint[]) {
  const byDate = new Map<
    string,
    { date: string; primary: number | null; secondary: number | null }
  >();

  for (const p of points) {
    const isPrimary =
      p.aaa_series === PRIMARY_PAIR.aaa && p.mezz_series === PRIMARY_PAIR.mezz;
    const isSecondary =
      p.aaa_series === SECONDARY_PAIR.aaa && p.mezz_series === SECONDARY_PAIR.mezz;
    if (!isPrimary && !isSecondary) continue;

    let row = byDate.get(p.date);
    if (!row) {
      row = { date: p.date, primary: null, secondary: null };
      byDate.set(p.date, row);
    }
    if (isPrimary) row.primary = p.price_ratio;
    if (isSecondary) row.secondary = p.price_ratio;
  }

  return Array.from(byDate.values()).sort((a, b) => a.date.localeCompare(b.date));
}

function median(values: number[]): number | null {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

function ChartTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: { payload: ReturnType<typeof toChartData>[number] }[];
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  const row = payload[0].payload;
  return (
    <TooltipShell title={row.date ? fmtDate(row.date) : label}>
      <div style={{ color: COLORS.neutral }}>
        JBBB/JAAA: {row.primary != null ? row.primary.toFixed(4) : '—'}
      </div>
      {row.secondary != null && (
        <div style={{ color: COLORS.textSecondary }}>
          JBBB/CLOA: {row.secondary.toFixed(4)}
        </div>
      )}
    </TooltipShell>
  );
}

function SpreadProxyPanel() {
  const { data, isLoading, isError } = useQuery({
    queryKey: qk.cloSpreadProxy,
    queryFn: () => getCloSpreadProxy().then((r) => r.data),
    staleTime: 5 * 60_000,
  });

  const chartData = useMemo(() => toChartData(data ?? []), [data]);

  const primarySeries = useMemo(
    () =>
      chartData
        .map((r) => r.primary)
        .filter((v): v is number => v != null),
    [chartData],
  );
  const med = useMemo(() => median(primarySeries), [primarySeries]);

  const latest = primarySeries.length ? primarySeries[primarySeries.length - 1] : null;
  const first = primarySeries.length ? primarySeries[0] : null;
  const delta = latest != null && first != null ? latest - first : null;
  const deltaPct = latest != null && first != null && first !== 0
    ? ((latest - first) / first) * 100
    : null;

  const subtitle = (
    <span>
      MEZZ vs AAA PRICE RATIO · 1Y · STRESS WHEN RATIO FALLS
      {latest != null && (
        <>
          {' · LATEST '}
          <span className="mono" style={{ color: COLORS.textPrimary }}>
            {latest.toFixed(4)}
          </span>
          {delta != null && deltaPct != null && (
            <span
              className="mono"
              style={{
                marginLeft: 6,
                color: delta >= 0 ? 'var(--positive)' : 'var(--negative)',
              }}
            >
              {delta >= 0 ? '+' : ''}
              {deltaPct.toFixed(2)}%
            </span>
          )}
        </>
      )}
    </span>
  );

  return (
    <Panel title="CLO Spread Proxy (JBBB / JAAA)" subtitle={subtitle}>
      {isLoading ? (
        <LoadingCursor />
      ) : isError ? (
        <EmptyState message="FAILED TO LOAD CLO SPREAD PROXY" />
      ) : chartData.length === 0 ? (
        <EmptyState message="NO CLO ETF DATA YET — RUN MARKET FETCH" />
      ) : (
        <ResponsiveContainer width="100%" height={240}>
          <LineChart data={chartData} margin={{ top: 6, right: 12, bottom: 0, left: 0 }}>
            <CartesianGrid stroke={COLORS.border} vertical={false} />
            <XAxis
              dataKey="date"
              tick={{ fill: COLORS.axis, fontSize: 10 }}
              stroke={COLORS.axis}
              minTickGap={48}
              tickFormatter={(v) => fmtDate(String(v))}
            />
            <YAxis
              tick={{ fill: COLORS.axis, fontSize: 10 }}
              stroke={COLORS.axis}
              width={52}
              domain={['auto', 'auto']}
              tickFormatter={(v) => Number(v).toFixed(3)}
            />
            <Tooltip content={<ChartTooltip />} cursor={{ stroke: COLORS.borderBright }} />
            {med != null && (
              <ReferenceLine
                y={med}
                stroke={COLORS.textDim}
                strokeDasharray="3 3"
                label={{
                  value: `median ${med.toFixed(3)}`,
                  position: 'insideTopRight',
                  fill: COLORS.textSecondary,
                  fontSize: 10,
                }}
              />
            )}
            <Line
              type="monotone"
              dataKey="secondary"
              name="JBBB/CLOA"
              stroke={COLORS.textDim}
              strokeWidth={1}
              dot={false}
              isAnimationActive={false}
              connectNulls
            />
            <Line
              type="monotone"
              dataKey="primary"
              name="JBBB/JAAA"
              stroke={COLORS.neutral}
              strokeWidth={1.5}
              dot={false}
              isAnimationActive={false}
              connectNulls
            />
          </LineChart>
        </ResponsiveContainer>
      )}
    </Panel>
  );
}

export default function CloStressPanel() {
  // The recent-CLO-filings list lived here too; it duplicated the same data
  // the ABS page's EDGAR FEED already surfaces, so we render just the spread
  // proxy here now.
  return <SpreadProxyPanel />;
}
