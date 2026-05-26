// HY OAS (ICE BofA) — spread chart with range toggle and current level in bps.

import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getFredHistory } from '../../lib/api';
import { qk } from '../../lib/queryKeys';
import { COLORS } from '../../lib/colors';
import Panel from '../shared/Panel';
import RangeToggle from '../shared/RangeToggle';
import LoadingCursor from '../shared/LoadingCursor';
import EmptyState from '../shared/EmptyState';
import TimeSeriesChart from '../charts/TimeSeriesChart';

const SERIES = 'BAMLH0A0HYM2';
const RANGES = ['1M', '3M', '1Y'] as const;
type Range = (typeof RANGES)[number];
const TRADING_DAYS: Record<Range, number> = { '1M': 22, '3M': 65, '1Y': 252 };

export default function HYOASPanel() {
  const [range, setRange] = useState<Range>('1Y');

  // Fetch ~1Y once; slice client-side for shorter ranges.
  const { data, isLoading, isError } = useQuery({
    queryKey: qk.fredHistory(SERIES),
    queryFn: () => getFredHistory(SERIES, 300).then((r) => r.data),
  });

  const sliced = useMemo(() => {
    if (!data) return [];
    return data.slice(-TRADING_DAYS[range]);
  }, [data, range]);

  const current = data?.length ? data[data.length - 1].value : null;
  const subtitle =
    current != null ? `${(current * 100).toFixed(0)}bps (${current.toFixed(2)}%)` : undefined;

  return (
    <Panel
      title="HY OAS"
      subtitle={subtitle}
      actions={<RangeToggle options={RANGES} value={range} onChange={setRange} />}
    >
      {isLoading ? (
        <LoadingCursor />
      ) : isError || !data?.length ? (
        <EmptyState message="NO FRED DATA — SET FRED_API_KEY AND REFRESH" />
      ) : (
        <TimeSeriesChart
          data={sliced}
          color={COLORS.chartPrimary}
          valueFormatter={(v) => `${v.toFixed(2)}%`}
        />
      )}
    </Panel>
  );
}
