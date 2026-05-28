// Regulatory Pipeline Monitor — foundation stub. The regulatory worktree fills in:
//   - Agency filter chips (CFPB / OCC / FDIC / Fed / SEC)
//   - Action-type filter (RULE / PROPOSED_RULE / NOTICE / PRESS_RELEASE)
//   - List of regulatory actions with score dots, dates, abstract, source link
//   - REFRESH button — POST /api/regulatory/refresh (token-free)
//   - SCORE button   — POST /api/regulatory/score   (Claude tokens; manual only)
//
// Data path: /api/regulatory/actions with agency / action_type / min_score filters.

import Panel from '../shared/Panel';

export default function RegulatoryFeedPanel() {
  return (
    <Panel title="Regulatory Pipeline" subtitle="Module not yet implemented">
      <div className="muted" style={{ padding: 8, fontSize: 12 }}>
        Pending implementation in the regulatory-flow worktree.
      </div>
    </Panel>
  );
}
