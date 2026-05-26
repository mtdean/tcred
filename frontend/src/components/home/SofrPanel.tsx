// SOFR rates: 1M / 3M term averages + trailing 1Y computed from the SOFR Index.

import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getSofrRates } from '../../lib/api';
import { qk } from '../../lib/queryKeys';
import { COLORS } from '../../lib/colors';
import Panel from '../shared/Panel';
import RangeToggle from '../shared/RangeToggle';
import LoadingCursor from '../shared/LoadingCursor';
import EmptyState from '../shared/EmptyState';
import MultiLineChart from '../charts/MultiLineChart';
import type { SeriesDef } from '../charts/MultiLineChart';

const RANGES = ['1M', '3M', '1Y'] as const;
type Range = (typeof RANGES)[number];
const TRADING_DAYS: Record<Range, number> = { '1M': 22, '3M': 65, '1Y': 252 };

const SERIES: SeriesDef[] = [
  { key: 'm1', name: '1M', color: COLORS.chartPrimary, width: 1.5 },
  { key: 'm3', name: '3M', color: COLORS.chartSecondary, width: 1.5 },
  { key: 'y1', name: '1Y', color: COLORS.chartTertiary, width: 1.5 },
];

export default function SofrPanel() {
  const [range, setRange] = useState<Range>('1Y');

  const { data, isLoading, isError } = useQuery({
    queryKey: qk.sofr,
    queryFn: () => getSofrRates(300).then((r) => r.data),
  });

  const sliced = useMemo(() => {
    if (!data) return [];
    return data.slice(-TRADING_DAYS[range]) as unknown as Record<string, unknown>[];
  }, [data, range]);

  const last = data?.length ? data[data.length - 1] : null;
  const fmt = (v: number | null | undefined) => (v == null ? '—' : `${v.toFixed(2)}%`);
  const subtitle = last
    ? `1M ${fmt(last.m1)} · 3M ${fmt(last.m3)} · 1Y ${fmt(last.y1)}`
    : undefined;

  return (
    <Panel
      title="SOFR Rates"
      subtitle={subtitle}
      actions={<RangeToggle options={RANGES} value={range} onChange={setRange} />}
    >
      {isLoading ? (
        <LoadingCursor />
      ) : isError || !data?.length ? (
        <EmptyState message="NO SOFR DATA — SET FRED_API_KEY AND REFRESH" />
      ) : (
        <MultiLineChart data={sliced} series={SERIES} valueFormatter={(v) => `${v.toFixed(2)}%`} />
      )}
    </Panel>
  );
}
