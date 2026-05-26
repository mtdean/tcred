// Bottom bar: live feed health, DB row counts. Polls the backend.

import { useQuery } from '@tanstack/react-query';
import { getStatus } from '../../lib/api';
import { qk } from '../../lib/queryKeys';

export default function StatusBar() {
  const { data, isError, isLoading } = useQuery({
    queryKey: qk.status,
    queryFn: () => getStatus().then((r) => r.data),
    refetchInterval: 30_000,
  });

  if (isLoading) {
    return (
      <footer className="statusbar">
        <span className="loading-cursor">CONNECTING</span>
      </footer>
    );
  }

  if (isError || !data) {
    return (
      <footer className="statusbar">
        <span>
          <span className="dot dot-dead" />
          BACKEND UNREACHABLE — is the API running on :8000?
        </span>
      </footer>
    );
  }

  const { feeds, articles, metrics, edgar_filings } = data;
  const allLive = feeds.total > 0 && feeds.live === feeds.total;
  const feedDot = feeds.total === 0 ? 'dot-warn' : allLive ? 'dot-live' : 'dot-warn';

  return (
    <footer className="statusbar">
      <span>
        <span className={`dot ${feedDot}`} />
        FEEDS {feeds.live}/{feeds.total} LIVE
      </span>
      <span>
        ARTICLES <span className="mono">{articles.total.toLocaleString()}</span>
        <span className="dim"> ({articles.scored.toLocaleString()} scored)</span>
      </span>
      <span>
        METRICS <span className="mono">{metrics.toLocaleString()}</span>
      </span>
      <span>
        EDGAR <span className="mono">{edgar_filings.toLocaleString()}</span>
      </span>
    </footer>
  );
}
