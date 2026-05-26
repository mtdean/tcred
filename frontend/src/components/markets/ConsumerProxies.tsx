// Consumer-credit equity proxies (SYF/COF/AXP/DFS) — same snapshot format.

import type { MarketRow } from '../../lib/types';
import Panel from '../shared/Panel';
import EmptyState from '../shared/EmptyState';
import MarketSnapshotTable from './MarketSnapshotTable';

export default function ConsumerProxies({ rows }: { rows: MarketRow[] }) {
  return (
    <Panel title="Consumer Credit Proxies" subtitle="EQUITY PROXIES — NOT INVESTMENT ADVICE">
      {rows.length === 0 ? (
        <EmptyState message="NO DATA" />
      ) : (
        <MarketSnapshotTable rows={rows} grouped={false} />
      )}
    </Panel>
  );
}
