// BDC Portfolio Monitor — foundation stub. The bdc worktree fills in:
//   - Aggregate non-accrual trend chart (multi-line: all BDCs + average)
//   - Mark-to-cost trend chart
//   - Per-BDC summary table (latest period)
//   - Individual non-accrual holdings table
//
// Data path: /api/bdc/nonaccrual-trend, /api/bdc/summary, /api/bdc/nonaccruals.
// Refresh button hits POST /api/bdc/refresh (token-free SEC bulk download).

import Panel from '../shared/Panel';

export default function BDCMonitorPanel() {
  return (
    <Panel title="BDC Portfolio Monitor" subtitle="Module not yet implemented">
      <div className="muted" style={{ padding: 8, fontSize: 12 }}>
        Pending implementation in the bdc-portfolio-monitor worktree.
      </div>
    </Panel>
  );
}
