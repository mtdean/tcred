import { useQuery } from '@tanstack/react-query';
import { getMarketSnapshot } from '../lib/api';
import { qk } from '../lib/queryKeys';
import Panel from '../components/shared/Panel';
import LoadingCursor from '../components/shared/LoadingCursor';
import EmptyState from '../components/shared/EmptyState';
import MarketSnapshotTable from '../components/markets/MarketSnapshotTable';
import SpreadCharts from '../components/markets/SpreadCharts';
import ConsumerProxies from '../components/markets/ConsumerProxies';

export default function MarketsPage() {
  const { data, isLoading, isError } = useQuery({
    queryKey: qk.marketSnapshot,
    queryFn: () => getMarketSnapshot().then((r) => r.data),
    refetchInterval: 60_000,
  });

  const rows = data ?? [];
  const consumer = rows.filter((r) => r.category === 'consumer_credit_proxy');
  const main = rows.filter((r) => r.category !== 'consumer_credit_proxy');

  return (
    <div className="stack">
      <Panel title="Market Snapshot" subtitle={rows.length ? `${rows.length} INSTRUMENTS` : undefined}>
        {isLoading ? (
          <LoadingCursor />
        ) : isError || rows.length === 0 ? (
          <EmptyState message="NO MARKET DATA" />
        ) : (
          <MarketSnapshotTable rows={main} />
        )}
      </Panel>

      <SpreadCharts />

      <ConsumerProxies rows={consumer} />
    </div>
  );
}
