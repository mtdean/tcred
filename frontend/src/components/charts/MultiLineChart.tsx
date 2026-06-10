// Reusable multi-series time-series line chart, Bloomberg-styled.
// Generic over row shape: pass series defs (key/name/color) and an x accessor.

import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { COLORS } from '../../lib/colors';
import { fmtDate, seriesReference } from '../../lib/utils';
import TooltipShell from './TooltipShell';

export interface SeriesDef {
  key: string;
  name: string;
  color: string;
  width?: number;
  // Underlying FRED series id; when it resolves to a FRED page the legend label
  // becomes a link. Computed/ingested series leave this undefined (no link).
  seriesId?: string;
}

// A [start, end] x-axis span to shade (e.g. an NBER recession). Both endpoints
// are values on the x-axis domain (dates as 'YYYY-MM-DD' strings here).
export interface ShadedInterval {
  start: string;
  end: string;
}

interface Props {
  data: Record<string, unknown>[];
  series: SeriesDef[];
  xKey?: string;
  height?: number;
  valueFormatter?: (v: number) => string;
  xFormatter?: (v: string) => string;
  // Optional gray bands (e.g. NBER recessions). Left undefined for panels that
  // don't want shading, so they're entirely unaffected.
  shadedIntervals?: ShadedInterval[];
}

function TooltipBox({
  active,
  payload,
  label,
  fmt,
  xFmt,
}: {
  active?: boolean;
  payload?: { name?: string; value?: number; color?: string }[];
  label?: string;
  fmt: (v: number) => string;
  xFmt: (v: string) => string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <TooltipShell title={xFmt(label ?? '')}>
      {payload.map((p) => (
        <div key={p.name} style={{ color: p.color }}>
          {p.name}: {p.value != null ? fmt(p.value) : '—'}
        </div>
      ))}
    </TooltipShell>
  );
}

export default function MultiLineChart({
  data,
  series,
  xKey = 'date',
  height = 240,
  valueFormatter = (v) => v.toFixed(2),
  xFormatter = (v) => fmtDate(v),
  shadedIntervals,
}: Props) {
  // Legend label → FRED page for raw series, methodology doc for computed ones.
  // Inherits the legend's color/weight so the appearance is unchanged.
  const refByKey = new Map(series.map((s) => [s.key, seriesReference(s.seriesId)]));
  const renderLegend = (value: string, entry: { dataKey?: unknown }) => {
    const ref = refByKey.get(String(entry?.dataKey ?? ''));
    if (!ref) return value;
    return (
      <a
        href={ref.url}
        target="_blank"
        rel="noreferrer"
        title={ref.title}
        style={{ color: 'inherit', textDecoration: 'none' }}
      >
        {value}
      </a>
    );
  };
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 6, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={COLORS.border} vertical={false} />
        {/* Recession (or other) bands sit behind the grid lines and series. */}
        {shadedIntervals?.map((iv) => (
          <ReferenceArea
            key={`${iv.start}-${iv.end}`}
            x1={iv.start}
            x2={iv.end}
            fill={COLORS.textSecondary}
            fillOpacity={0.12}
            stroke="none"
            ifOverflow="hidden"
          />
        ))}
        <XAxis
          dataKey={xKey}
          tick={{ fill: COLORS.axis, fontSize: 10 }}
          tickFormatter={xFormatter}
          minTickGap={48}
          stroke={COLORS.axis}
        />
        <YAxis
          tick={{ fill: COLORS.axis, fontSize: 10 }}
          tickFormatter={(v) => valueFormatter(v)}
          width={48}
          stroke={COLORS.axis}
          domain={['auto', 'auto']}
        />
        <Tooltip
          content={<TooltipBox fmt={valueFormatter} xFmt={xFormatter} />}
          cursor={{ stroke: COLORS.borderBright }}
        />
        <Legend wrapperStyle={{ fontSize: 10, letterSpacing: '0.06em' }} iconType="square" iconSize={8} formatter={renderLegend} />
        {series.map((s) => (
          <Line
            key={s.key}
            type="monotone"
            name={s.name}
            dataKey={s.key}
            stroke={s.color}
            strokeWidth={s.width ?? 1.5}
            dot={false}
            isAnimationActive={false}
            connectNulls
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
