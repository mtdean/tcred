// Charts for pluggable data sources (backend/data/sources.py). Any source that
// declares a `panel` gets one here with no frontend code — its series come
// from the shared metrics table via the same hook as the hand-built panels.

import { useQuery } from '@tanstack/react-query';
import { getSources } from '../../lib/api';
import { COLORS } from '../../lib/colors';
import { qk } from '../../lib/queryKeys';
import type { DataSource } from '../../lib/types';
import FredSeriesPanel from './FredSeriesPanel';
import type { FredSeriesDef } from '../charts/useFredSeries';
import { RANGES_DAILY, RANGES_MONTHLY, RANGES_QUARTERLY } from './ranges';

const SERIES_COLORS = [
  COLORS.chartPrimary,
  COLORS.chartSecondary,
  COLORS.chartTertiary,
  COLORS.chart6mo,
  COLORS.negative,
  COLORS.textSecondary,
];

const PRESETS = {
  daily: { ranges: RANGES_DAILY, defaultRange: '1Y', limit: 2700 },
  monthly: { ranges: RANGES_MONTHLY, defaultRange: '5Y', limit: 400 },
  quarterly: { ranges: RANGES_QUARTERLY, defaultRange: '10Y', limit: 200 },
} as const;

function SourcePanel({ source }: { source: DataSource }) {
  const panel = source.panel!;
  const preset = PRESETS[panel.frequency] ?? PRESETS.monthly;
  const series: FredSeriesDef[] = panel.series.map((s, i) => ({
    seriesId: s.series_id,
    key: s.series_id,
    name: s.name,
    color: SERIES_COLORS[i % SERIES_COLORS.length],
  }));
  return (
    <FredSeriesPanel
      title={panel.title}
      subtitle={panel.subtitle || source.description.toUpperCase()}
      series={series}
      ranges={[...preset.ranges]}
      defaultRange={preset.defaultRange}
      unit={panel.unit}
      decimals={panel.decimals}
      limit={preset.limit}
    />
  );
}

/** Renders `header` + a grid of plugin panels, or nothing if none declare one. */
export default function PluginSourcePanels({ header }: { header: React.ReactNode }) {
  const { data } = useQuery({
    queryKey: qk.sources,
    queryFn: () => getSources().then((r) => r.data),
    staleTime: 10 * 60_000,
  });
  const withPanels = (data?.sources ?? []).filter((s) => s.enabled && s.panel);
  if (withPanels.length === 0) return null;
  return (
    <>
      {header}
      <div className="grid-2">
        {withPanels.map((s) => (
          <SourcePanel key={s.id} source={s} />
        ))}
      </div>
    </>
  );
}
