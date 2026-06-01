// App header: title, tab nav, contextual refresh (+ last-refresh time for the
// current tab), live clock. The refresh button reads the active route and
// fires the right backend pull; on pages with no upstream pull (Analyst,
// Watchlists, Issuer pivot) the button hides entirely.

import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { RefreshCw } from 'lucide-react';
import { getStatus } from '../../lib/api';
import { qk } from '../../lib/queryKeys';
import { staticDisabledProps } from '../../lib/staticMode';
import { clockString, fmtRelative } from '../../lib/utils';
import { useTabRefresh } from '../../lib/useTabRefresh';
import TabNav from './TabNav';

export default function TopBar() {
  const [clock, setClock] = useState(clockString());

  useEffect(() => {
    const id = setInterval(() => setClock(clockString()), 1000);
    return () => clearInterval(id);
  }, []);

  // Shares cache with the StatusBar query — no extra request.
  const { data: status } = useQuery({
    queryKey: qk.status,
    queryFn: () => getStatus().then((r) => r.data),
    refetchInterval: 30_000,
  });

  const { spec, isPending, refresh } = useTabRefresh();

  const lastRefresh =
    spec && status ? status[spec.metaKey] : undefined;

  return (
    <header className="topbar">
      <span className="topbar-title">{'<TCRED>'}</span>
      <TabNav />
      <span className="topbar-spacer" />
      {spec && (
        <span className="topbar-meta" style={{ whiteSpace: 'nowrap' }}>
          {spec.shortLabel}:{' '}
          <span style={{ color: lastRefresh ? 'var(--text-secondary)' : 'var(--warning)' }}>
            {lastRefresh ? fmtRelative(lastRefresh) : 'never'}
          </span>
        </span>
      )}
      {spec && (
        <button
          className="btn"
          onClick={refresh}
          disabled={isPending}
          title={spec.label}
          style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
          {...staticDisabledProps()}
        >
          <RefreshCw
            size={12}
            style={isPending ? { animation: 'spin 1s linear infinite' } : undefined}
          />
          {isPending ? 'REFRESHING' : spec.label}
        </button>
      )}
      <span className="topbar-meta mono">{clock}</span>
      <style>{'@keyframes spin { to { transform: rotate(360deg); } }'}</style>
    </header>
  );
}
