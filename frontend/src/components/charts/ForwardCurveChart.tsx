// Treasury forward curve: today vs 6mo ago vs 1yr ago, x-axis = tenor.

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
import type { ForwardCurveData } from '../../lib/types';

interface Props {
  data: ForwardCurveData;
  height?: number;
}

const SERIES = [
  { key: 'today', name: 'TODAY', color: COLORS.chartPrimary, width: 2 },
  { key: 'six', name: '6MO AGO', color: COLORS.chart6mo, width: 1.5 },
  { key: 'one', name: '1YR AGO', color: COLORS.chart12mo, width: 1.5 },
] as const;

function TooltipBox({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: { name?: string; value?: number; color?: string }[];
  label?: string;
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
      <div style={{ color: COLORS.textSecondary }}>{label}</div>
      {payload.map((p) => (
        <div key={p.name} style={{ color: p.color }}>
          {p.name}: {p.value != null ? `${p.value.toFixed(2)}%` : '—'}
        </div>
      ))}
    </div>
  );
}

export default function ForwardCurveChart({ data, height = 240 }: Props) {
  const rows = data.tenors.map((tenor) => ({
    tenor,
    today: data.today[tenor],
    six: data.six_months_ago[tenor],
    one: data.one_year_ago[tenor],
  }));

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={rows} margin={{ top: 6, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={COLORS.border} vertical={false} />
        <XAxis dataKey="tenor" tick={{ fill: COLORS.axis, fontSize: 10 }} stroke={COLORS.axis} />
        <YAxis
          tick={{ fill: COLORS.axis, fontSize: 10 }}
          tickFormatter={(v) => `${v.toFixed(1)}`}
          width={40}
          stroke={COLORS.axis}
          domain={['auto', 'auto']}
        />
        <Tooltip content={<TooltipBox />} cursor={{ stroke: COLORS.borderBright }} />
        <Legend
          wrapperStyle={{ fontSize: 10, letterSpacing: '0.06em' }}
          iconType="square"
          iconSize={8}
        />
        {SERIES.map((s) => (
          <Line
            key={s.key}
            type="monotone"
            name={s.name}
            dataKey={s.key}
            stroke={s.color}
            strokeWidth={s.width}
            dot={false}
            isAnimationActive={false}
            connectNulls
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
