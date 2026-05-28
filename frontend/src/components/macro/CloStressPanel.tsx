// CLO Stress Monitor — foundation stub. The clo worktree fills in:
//   - JBBB/JAAA price-ratio time series (stress when ratio falls)
//   - Recent CLO-tagged EDGAR filings list
//   - ETF level callouts (JAAA, JBBB, CLOA, CLOZ latest %Δ)
//
// Data path: /api/clo/spread-proxy, /api/clo/filings.
// CLO ETF tickers and CLO EDGAR keywords added to data_sources.yaml.

import Panel from '../shared/Panel';

export default function CloStressPanel() {
  return (
    <Panel title="CLO Stress" subtitle="Module not yet implemented">
      <div className="muted" style={{ padding: 8, fontSize: 12 }}>
        Pending implementation in the clo-stress-monitor worktree.
      </div>
    </Panel>
  );
}
