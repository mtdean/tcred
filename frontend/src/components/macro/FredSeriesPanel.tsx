// Generic macro panel: several FRED series on one chart with a range toggle.

import { useMemo, useState } from 'react';
import { sliceByYears } from '../../lib/utils';
import Panel from '../shared/Panel';
import RangeToggle from '../shared/RangeToggle';
import LoadingCursor from '../shared/LoadingCursor';
import EmptyState from '../shared/EmptyState';
import MultiLineChart from '../charts/MultiLineChart';
import type { SeriesDef, ShadedInterval } from '../charts/MultiLineChart';
import { useFredSeries } from '../charts/useFredSeries';
import type { FredSeriesDef } from '../charts/useFredSeries';

interface RangeOpt {
  label: string;
  years: number | null;
}

interface Props {
  title: string;
  subtitle?: string;
  series: FredSeriesDef[];
  ranges: RangeOpt[];
  defaultRange: string;
  height?: number;
  limit?: number;
  decimals?: number;
  unit?: 'pct' | 'plain';
  // Optional gray bands (e.g. NBER recessions) drawn behind the series. Clipped
  // to the visible range so the recharts ReferenceArea endpoints stay on-domain.
  shadedIntervals?: ShadedInterval[];
}

export default function FredSeriesPanel({
  title,
  subtitle,
  series,
  ranges,
  defaultRange,
  height = 220,
  limit = 240,
  decimals = 1,
  unit = 'pct',
  shadedIntervals,
}: Props) {
  const [range, setRange] = useState(defaultRange);
  const { rows, isLoading, isError } = useFredSeries(series, limit);

  const yearsFor = (label: string) => ranges.find((r) => r.label === label)?.years ?? null;
  const sliced = useMemo(
    () => sliceByYears(rows as { date: string }[], yearsFor(range)) as Record<string, unknown>[],
    [rows, range],
  );

  // Clip shaded bands to the visible window so ReferenceArea endpoints land on
  // the rendered x-domain (recharts won't draw a band that starts off-axis).
  const visibleIntervals = useMemo<ShadedInterval[] | undefined>(() => {
    if (!shadedIntervals?.length || sliced.length === 0) return undefined;
    const lo = String((sliced[0] as { date: string }).date);
    const hi = String((sliced[sliced.length - 1] as { date: string }).date);
    return shadedIntervals
      .filter((iv) => iv.end >= lo && iv.start <= hi)
      .map((iv) => ({ start: iv.start < lo ? lo : iv.start, end: iv.end > hi ? hi : iv.end }));
  }, [shadedIntervals, sliced]);

  const chartSeries: SeriesDef[] = series.map((s) => ({ key: s.key, name: s.name, color: s.color }));
  const fmt = (v: number) => (unit === 'pct' ? `${v.toFixed(decimals)}%` : v.toFixed(decimals));

  return (
    <Panel
      title={title}
      subtitle={subtitle}
      actions={
        <RangeToggle
          options={ranges.map((r) => r.label)}
          value={range}
          onChange={setRange}
        />
      }
    >
      {isLoading ? (
        <LoadingCursor />
      ) : isError || rows.length === 0 ? (
        <EmptyState message="NO FRED DATA" />
      ) : (
        <MultiLineChart
          data={sliced}
          series={chartSeries}
          height={height}
          valueFormatter={fmt}
          shadedIntervals={visibleIntervals}
        />
      )}
    </Panel>
  );
}
