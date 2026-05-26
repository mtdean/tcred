// Recharts line/area wrapper, Bloomberg-styled: dark grid, 10%-opacity fill,
// monospace tooltip. Used by spread/rate panels.

import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { MetricPoint } from '../../lib/types';
import { COLORS } from '../../lib/colors';
import { fmtDate } from '../../lib/utils';

interface Props {
  data: MetricPoint[];
  color?: string;
  height?: number;
  valueFormatter?: (v: number) => string;
}

function TooltipBox({
  active,
  payload,
  label,
  fmt,
}: {
  active?: boolean;
  payload?: { value?: number }[];
  label?: string;
  fmt: (v: number) => string;
}) {
  if (!active || !payload?.length || payload[0].value == null) return null;
  return (
    <div
      style={{
        background: COLORS.bgPanel,
        border: `1px solid ${COLORS.borderBright}`,
        padding: '4px 8px',
        fontSize: 11,
        fontVariantNumeric: 'tabular-nums',
      }}
    >
      <div style={{ color: COLORS.textSecondary }}>{fmtDate(label)}</div>
      <div style={{ color: COLORS.textPrimary }}>{fmt(payload[0].value)}</div>
    </div>
  );
}

export default function TimeSeriesChart({
  data,
  color = COLORS.chartPrimary,
  height = 200,
  valueFormatter = (v) => v.toFixed(2),
}: Props) {
  const gradId = `fill-${color.replace('#', '')}`;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.1} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke={COLORS.border} vertical={false} />
        <XAxis
          dataKey="date"
          tick={{ fill: COLORS.axis, fontSize: 10 }}
          tickFormatter={(d) => fmtDate(d)}
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
          content={<TooltipBox fmt={valueFormatter} />}
          cursor={{ stroke: COLORS.borderBright }}
        />
        <Area
          type="monotone"
          dataKey="value"
          stroke={color}
          strokeWidth={1.5}
          fill={`url(#${gradId})`}
          dot={false}
          isAnimationActive={false}
          connectNulls
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
