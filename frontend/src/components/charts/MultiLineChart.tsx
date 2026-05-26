// Reusable multi-series time-series line chart, Bloomberg-styled.
// Generic over row shape: pass series defs (key/name/color) and an x accessor.

import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { COLORS } from '../../lib/colors';
import { fmtDate } from '../../lib/utils';

export interface SeriesDef {
  key: string;
  name: string;
  color: string;
  width?: number;
}

interface Props {
  data: Record<string, unknown>[];
  series: SeriesDef[];
  xKey?: string;
  height?: number;
  valueFormatter?: (v: number) => string;
  xFormatter?: (v: string) => string;
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
    <div
      style={{
        background: COLORS.bgPanel,
        border: `1px solid ${COLORS.borderBright}`,
        padding: '4px 8px',
        fontSize: 11,
        fontVariantNumeric: 'tabular-nums',
      }}
    >
      <div style={{ color: COLORS.textSecondary }}>{xFmt(label ?? '')}</div>
      {payload.map((p) => (
        <div key={p.name} style={{ color: p.color }}>
          {p.name}: {p.value != null ? fmt(p.value) : '—'}
        </div>
      ))}
    </div>
  );
}

export default function MultiLineChart({
  data,
  series,
  xKey = 'date',
  height = 240,
  valueFormatter = (v) => v.toFixed(2),
  xFormatter = (v) => fmtDate(v),
}: Props) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 6, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={COLORS.border} vertical={false} />
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
        <Legend wrapperStyle={{ fontSize: 10, letterSpacing: '0.06em' }} iconType="square" iconSize={8} />
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
