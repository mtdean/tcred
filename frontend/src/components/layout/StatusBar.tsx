// Bottom bar: live feed health, DB row counts, data-freshness rollup.

import { useQuery } from '@tanstack/react-query';
import { getFreshness, getStatus } from '../../lib/api';
import { qk } from '../../lib/queryKeys';

export default function StatusBar() {
  const { data, isError, isLoading } = useQuery({
    queryKey: qk.status,
    queryFn: () => getStatus().then((r) => r.data),
    refetchInterval: 30_000,
  });

  const { data: freshness } = useQuery({
    queryKey: qk.freshness,
    queryFn: () => getFreshness().then((r) => r.data),
    refetchInterval: 5 * 60_000,
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

  const { feeds, articles, metrics, edgar_filings, jobs } = data;
  const allLive = feeds.total > 0 && feeds.live === feeds.total;
  const feedDot = feeds.total === 0 ? 'dot-warn' : allLive ? 'dot-live' : 'dot-warn';

  // Latest run per scheduled job — surface failures that otherwise only land
  // in the server log (e.g. a fetch dying during startup's initial pull).
  const failedJobs = (jobs ?? []).filter((j) => j.status === 'error');
  const jobsTitle = failedJobs.length
    ? failedJobs.map((j) => `${j.job_id}: ${j.error ?? 'unknown error'}`).join('\n')
    : 'Latest run of every scheduled job succeeded';

  const s = freshness?.summary;
  const stale = (s?.stale ?? 0) + (s?.missing ?? 0);
  const dead = s?.dead ?? 0;
  // Color the data badge by the worst current status (dead > stale > clean).
  const dataDotClass = dead > 0 ? 'dot-dead' : stale > 0 ? 'dot-warn' : 'dot-live';
  const dataLabel =
    dead > 0
      ? `${dead} DEAD${stale > 0 ? ` · ${stale} STALE` : ''}`
      : stale > 0
        ? `${stale} STALE`
        : 'ALL FRESH';

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
      {freshness && (
        <span title="Per-series staleness across configured FRED series">
          <span className={`dot ${dataDotClass}`} />
          DATA {dataLabel}
        </span>
      )}
      {(jobs ?? []).length > 0 && (
        <span title={jobsTitle}>
          <span className={`dot ${failedJobs.length ? 'dot-dead' : 'dot-live'}`} />
          JOBS{' '}
          {failedJobs.length
            ? `${failedJobs.length} FAILED (${failedJobs.map((j) => j.job_id.toUpperCase()).join(', ')})`
            : 'OK'}
        </span>
      )}
    </footer>
  );
}
