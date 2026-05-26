// Credit-spread charts: HY OAS, IG OAS, HY effective yield. Each with a range
// toggle and a current value + 3yr percentile in the header.

import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getFredHistory } from '../../lib/api';
import { qk } from '../../lib/queryKeys';
import { COLORS } from '../../lib/colors';
import { ordinal, percentileRank, sliceByDays } from '../../lib/utils';
import Panel from '../shared/Panel';
import RangeToggle from '../shared/RangeToggle';
import LoadingCursor from '../shared/LoadingCursor';
import EmptyState from '../shared/EmptyState';
import TimeSeriesChart from '../charts/TimeSeriesChart';

const RANGES = ['1M', '3M', '6M', '1Y', '3Y', 'MAX'] as const;
type Range = (typeof RANGES)[number];
const RANGE_DAYS: Record<Range, number | null> = {
  '1M': 30, '3M': 91, '6M': 182, '1Y': 365, '3Y': 1095, MAX: null,
};

interface SpreadDef {
  seriesId: string;
  title: string;
  color: string;
  unit: 'bps' | 'pct';
}

function SpreadChart({ def }: { def: SpreadDef }) {
  const [range, setRange] = useState<Range>('1Y');
  const { data, isLoading, isError } = useQuery({
    queryKey: qk.fredHistory(def.seriesId),
    queryFn: () => getFredHistory(def.seriesId, 800).then((r) => r.data),
  });

  const sliced = useMemo(() => sliceByDays(data ?? [], RANGE_DAYS[range]), [data, range]);

  const values = (data ?? []).map((d) => d.value).filter((v): v is number => v != null);
  const current = values.length ? values[values.length - 1] : null;
  const pctile = current != null ? percentileRank(values, current) : null;

  const currentLabel =
    current == null
      ? '—'
      : def.unit === 'bps'
        ? `${(current * 100).toFixed(0)}bps`
        : `${current.toFixed(2)}%`;
  const subtitle =
    current == null ? undefined : `${currentLabel}${pctile != null ? ` · ${ordinal(pctile)} %ile (3Y)` : ''}`;

  return (
    <Panel
      title={def.title}
      subtitle={subtitle}
      actions={<RangeToggle options={RANGES} value={range} onChange={setRange} />}
    >
      {isLoading ? (
        <LoadingCursor />
      ) : isError || !data?.length ? (
        <EmptyState message="NO DATA" />
      ) : (
        <TimeSeriesChart data={sliced} color={def.color} height={180} valueFormatter={(v) => `${v.toFixed(2)}%`} />
      )}
    </Panel>
  );
}

const SPREADS: SpreadDef[] = [
  { seriesId: 'BAMLH0A0HYM2', title: 'HY OAS', color: COLORS.chartPrimary, unit: 'bps' },
  { seriesId: 'BAMLC0A0CM', title: 'IG OAS', color: COLORS.chartSecondary, unit: 'bps' },
  { seriesId: 'BAMLH0A0HYM2EY', title: 'HY Effective Yield', color: COLORS.chartTertiary, unit: 'pct' },
];

export default function SpreadCharts() {
  return (
    <div style={{ display: 'grid', gap: 12, gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))' }}>
      {SPREADS.map((s) => (
        <SpreadChart key={s.seriesId} def={s} />
      ))}
    </div>
  );
}
