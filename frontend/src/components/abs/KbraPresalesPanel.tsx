// KBRA Presale Parser — foundation stub. The kbra worktree fills in:
//   - Table of parsed presales: deal, issuer, asset class, base CDR/CPR/loss,
//     CE by tranche (AAA/AA/A/BBB/BB)
//   - Asset-class filter
//   - PARSE button — POST /api/kbra/refresh (Claude tokens; manual only)
//   - Help text explaining the manual ingest path (drop PDFs into
//     backend/data/kbra/presales/)
//
// Data path: /api/kbra/presales with optional asset_class filter.

import Panel from '../shared/Panel';

export default function KbraPresalesPanel() {
  return (
    <Panel title="KBRA Presales" subtitle="Module not yet implemented">
      <div className="muted" style={{ padding: 8, fontSize: 12 }}>
        Pending implementation in the kbra-presale-parser worktree.
      </div>
    </Panel>
  );
}
