// SIFMA ABS issuance — placeholder. No public API; data is a manual import.
// See CLAUDE.md / FRONTEND.md Phase 3 for the planned scraper/CSV pipeline.

import Panel from '../shared/Panel';

const ASSET_CLASSES = [
  'auto', 'credit card', 'student loan', 'equipment',
  'home equity', 'manufactured housing', 'other',
];

export default function SifmaPanel() {
  return (
    <Panel title="SIFMA ABS Issuance Data" subtitle="MANUAL IMPORT REQUIRED">
      <div
        style={{
          height: 200,
          border: '1px dashed var(--border-bright)',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 8,
          color: 'var(--text-dim)',
        }}
      >
        <div style={{ letterSpacing: '0.06em' }}>MONTHLY ISSUANCE DATA — MANUAL IMPORT REQUIRED</div>
        <div className="prose" style={{ fontSize: 11, color: 'var(--text-secondary)', maxWidth: 480, textAlign: 'center' }}>
          SIFMA publishes no public API. Issuance data must be downloaded from sifma.org
          (or scraped) and imported. Chart renders here once data lands.
        </div>
      </div>
      <div className="muted" style={{ fontSize: 11, marginTop: 8 }}>
        Supported asset classes:{' '}
        {ASSET_CLASSES.map((c) => (
          <span key={c} className="cat-chip" style={{ color: 'var(--text-secondary)', marginRight: 4 }}>
            {c}
          </span>
        ))}
      </div>
    </Panel>
  );
}
