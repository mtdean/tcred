// Where the current value sits in its own trailing distribution.
// Color hint: extremes (top/bottom decile) read in --warning so the eye
// catches them; the middle reads neutral. Window label is small and dim.

import type { CSSProperties } from 'react';

interface Props {
  percentile: number; // 0-100
  windowLabel?: string; // e.g. "5y"
  nObs?: number;
  style?: CSSProperties;
}

function colorFor(p: number): string {
  if (p >= 90 || p <= 10) return 'var(--warning)';
  if (p >= 75 || p <= 25) return 'var(--text-primary)';
  return 'var(--text-secondary)';
}

export default function PercentileChip({ percentile, windowLabel = '5y', nObs, style }: Props) {
  if (!Number.isFinite(percentile)) return null;
  const color = colorFor(percentile);
  const title =
    nObs != null
      ? `${percentile.toFixed(0)}th percentile of ${windowLabel} (n=${nObs})`
      : `${percentile.toFixed(0)}th percentile of ${windowLabel}`;
  return (
    <span
      title={title}
      style={{
        display: 'inline-flex',
        alignItems: 'baseline',
        gap: 3,
        color,
        fontSize: 10,
        letterSpacing: 0.5,
        ...style,
      }}
    >
      <span className="mono">{percentile.toFixed(0)}</span>
      <span className="dim" style={{ fontSize: 9 }}>P · {windowLabel}</span>
    </span>
  );
}
