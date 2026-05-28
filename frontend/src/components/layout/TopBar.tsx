// App header: title, tab nav, manual refresh (+ last-refresh time), live clock.

import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { RefreshCw } from 'lucide-react';
import { getStatus, triggerRefresh } from '../../lib/api';
import { qk } from '../../lib/queryKeys';
import { staticDisabledProps } from '../../lib/staticMode';
import { clockString, fmtRelative } from '../../lib/utils';
import TabNav from './TabNav';

export default function TopBar() {
  const [clock, setClock] = useState(clockString());
  const queryClient = useQueryClient();

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

  const refresh = useMutation({
    mutationFn: triggerRefresh,
    onSuccess: () => {
      // Feed fetch/classify touches articles + status; refetch them.
      queryClient.invalidateQueries({ queryKey: ['articles'] });
      queryClient.invalidateQueries({ queryKey: ['articles-feed'] });
      queryClient.invalidateQueries({ queryKey: ['status'] });
      queryClient.invalidateQueries({ queryKey: ['feed-health'] });
    },
  });

  const lastRefresh = status?.last_news_refresh;

  return (
    <header className="topbar">
      <span className="topbar-title">{'<TCRED>'}</span>
      <TabNav />
      <span className="topbar-spacer" />
      <span className="topbar-meta" style={{ whiteSpace: 'nowrap' }}>
        NEWS:{' '}
        <span style={{ color: lastRefresh ? 'var(--text-secondary)' : 'var(--warning)' }}>
          {lastRefresh ? fmtRelative(lastRefresh) : 'never'}
        </span>
      </span>
      <button
        className="btn"
        onClick={() => refresh.mutate()}
        disabled={refresh.isPending}
        title="Fetch feeds and classify new articles (uses Claude tokens)"
        style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
        {...staticDisabledProps()}
      >
        <RefreshCw
          size={12}
          style={refresh.isPending ? { animation: 'spin 1s linear infinite' } : undefined}
        />
        {refresh.isPending ? 'REFRESHING' : 'REFRESH'}
      </button>
      <span className="topbar-meta mono">{clock}</span>
      <style>{'@keyframes spin { to { transform: rotate(360deg); } }'}</style>
    </header>
  );
}
