// Tiny status pill that surfaces data staleness. Renders nothing when the
// source is fresh — only "STALE" / "DEAD" / "MISSING" earn screen real estate.

import type { CSSProperties } from 'react';
import type { FreshnessStatus } from '../../lib/api';

const COLORS: Record<FreshnessStatus, string> = {
  fresh: 'var(--text-secondary)',
  stale: 'var(--warning)',
  dead: 'var(--danger, #d44)',
  missing: 'var(--text-secondary)',
};

interface Props {
  status: FreshnessStatus;
  daysSince?: number | null;
  frequency?: string;
  // When `compact`, we render an inline dot + days; otherwise a labelled chip.
  compact?: boolean;
  style?: CSSProperties;
}

export default function FreshnessChip({
  status,
  daysSince,
  frequency,
  compact = false,
  style,
}: Props) {
  if (status === 'fresh') return null;

  const color = COLORS[status];
  const title =
    status === 'missing'
      ? `No observations yet${frequency ? ` (expected ${frequency})` : ''}`
      : daysSince != null
        ? `${daysSince}d since last update${frequency ? ` · expected ${frequency}` : ''}`
        : status.toUpperCase();

  if (compact) {
    return (
      <span
        title={title}
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 4,
          color,
          fontSize: 10,
          letterSpacing: 1,
          ...style,
        }}
      >
        <span
          style={{
            display: 'inline-block',
            width: 6,
            height: 6,
            borderRadius: '50%',
            background: color,
          }}
        />
        {daysSince != null ? `${daysSince}D` : status.toUpperCase()}
      </span>
    );
  }

  return (
    <span
      title={title}
      className="cat-chip"
      style={{
        color,
        border: `1px solid ${color}`,
        padding: '0 4px',
        fontSize: 9,
        letterSpacing: 1,
        ...style,
      }}
    >
      {status.toUpperCase()}
      {daysSince != null && status !== 'missing' ? ` · ${daysSince}D` : ''}
    </span>
  );
}
