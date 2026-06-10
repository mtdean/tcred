// Shared Bloomberg-styled tooltip container for all recharts wrappers:
// dark panel, bright border, monospace tabular figures, secondary title line.

import type { ReactNode } from 'react';
import { COLORS } from '../../lib/colors';

export default function TooltipShell({
  title,
  children,
}: {
  title: ReactNode;
  children: ReactNode;
}) {
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
      <div style={{ color: COLORS.textSecondary }}>{title}</div>
      {children}
    </div>
  );
}
