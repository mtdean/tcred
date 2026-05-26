// Tiny inline sparkline — no axes, no tooltip. For table rows.

import { Line, LineChart, ResponsiveContainer, YAxis } from 'recharts';
import type { MetricPoint } from '../../lib/types';
import { COLORS } from '../../lib/colors';

interface Props {
  data: MetricPoint[];
  width?: number;
  height?: number;
  color?: string;
}

export default function Sparkline({ data, width = 70, height = 24, color }: Props) {
  if (!data || data.length < 2) {
    return <span className="dim" style={{ fontSize: 10 }}>—</span>;
  }
  // Color by net direction over the window unless overridden.
  const first = data[0].value ?? 0;
  const last = data[data.length - 1].value ?? 0;
  const stroke = color ?? (last >= first ? COLORS.positive : COLORS.negative);

  return (
    <div style={{ width, height }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 2, right: 1, bottom: 2, left: 1 }}>
          <YAxis hide domain={['dataMin', 'dataMax']} />
          <Line
            type="monotone"
            dataKey="value"
            stroke={stroke}
            strokeWidth={1}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
