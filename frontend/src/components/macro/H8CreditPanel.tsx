// Fed H.8 Bank Credit Monitor — foundation stub. The h8 worktree fills in:
//   - Summary table: each H.8 series with latest, WoW Δ, WoW %, YoY %
//   - Credit-impulse bar chart (quarterly QoQ change in TOTLL / GDP, annualized)
//   - Mini YoY % sparklines per series
//
// Data path: /api/h8/metrics, /api/h8/credit-impulse.
// Raw FRED series fetched by existing scheduler (h8_bank_credit category).

import Panel from '../shared/Panel';

export default function H8CreditPanel() {
  return (
    <Panel title="Bank Credit Supply (H.8)" subtitle="Module not yet implemented">
      <div className="muted" style={{ padding: 8, fontSize: 12 }}>
        Pending implementation in the h8-credit-monitor worktree.
      </div>
    </Panel>
  );
}
