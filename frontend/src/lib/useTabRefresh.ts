// Route-aware refresh: maps the current pathname to (a) a label for the
// TopBar button, (b) the background job to run, (c) the meta key on
// /api/status that records the last successful run for this scope, and
// (d) the React Query keys to invalidate so the UI repaints.
//
// Refresh is NON-BLOCKING server-side: POST /api/jobs/run/{job_id} returns a
// run_id immediately and the pull continues in a backend thread (recorded in
// job_runs). We poll the run until it finishes, so the button/pull spinner
// still reflects real progress — but the server stays responsive and the job
// survives the user navigating away or closing the app.
//
// Pages that are pure views over the existing DB (no upstream pull) — Analyst
// briefings, Watchlists — return `null` so the TopBar can hide the button.

import { useLocation } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { getJobRun, startJob } from './api';
import { qk } from './queryKeys';
import { showToast } from './toast';
import { apiErrorMessage } from './utils';
import type { StatusResponse } from './types';

export type LastRefreshKey = keyof Pick<
  StatusResponse,
  | 'last_news_refresh'
  | 'last_market_refresh'
  | 'last_fred_refresh'
  | 'last_abs_pricing_refresh'
  | 'last_abs_424b5_refresh'
  | 'last_bdc_refresh'
  | 'last_regulatory_refresh'
>;

export interface TabRefreshSpec {
  label: string;
  shortLabel: string;
  metaKey: LastRefreshKey;
  jobId: string;
  invalidateKeys: readonly (readonly unknown[])[];
}

const POLL_MS = 2500;
const TIMEOUT_MS = 10 * 60_000;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// Start the job, then poll its run until it completes. Resolves to the rows
// ingested (for the completion toast); throws on job error or timeout.
async function runJobToCompletion(jobId: string): Promise<number> {
  const { data: start } = await startJob(jobId);
  if (start.run_id < 0) return 0;
  const t0 = Date.now();
  for (;;) {
    await sleep(POLL_MS);
    const { data: run } = await getJobRun(start.run_id);
    if (run.status !== 'running') {
      if (run.status === 'error') {
        throw new Error(run.error?.split('\n')[0] ?? 'refresh failed');
      }
      return run.rows_ingested ?? 0;
    }
    if (Date.now() - t0 > TIMEOUT_MS) {
      throw new Error('refresh still running server-side — check back shortly');
    }
  }
}

// Map pathname → spec. Keep this co-located with the routes so the next time
// someone adds a tab they remember to wire up its refresh here too.
function specFor(pathname: string): TabRefreshSpec | null {
  // News + Home both rely on the news pull.
  if (pathname === '/' || pathname.startsWith('/news')) {
    return {
      label: 'REFRESH NEWS',
      shortLabel: 'NEWS',
      metaKey: 'last_news_refresh',
      jobId: 'feeds',
      // ['articles'] / ['articles-feed'] are prefixes covering every
      // parameterized query in those families.
      invalidateKeys: [['articles'], ['articles-feed'], qk.status, qk.feedHealth],
    };
  }

  if (pathname.startsWith('/markets')) {
    return {
      label: 'REFRESH MARKETS',
      shortLabel: 'MARKETS',
      metaKey: 'last_market_refresh',
      jobId: 'market',
      // ['percentiles'] prefix covers qk.percentilesBatch(ids, window).
      invalidateKeys: [['market'], ['percentiles'], qk.status],
    };
  }

  if (pathname.startsWith('/macro')) {
    return {
      label: 'REFRESH MACRO',
      shortLabel: 'MACRO',
      metaKey: 'last_fred_refresh',
      jobId: 'fred',
      // ['fred'] prefix covers latest, history, forward-curve and sofr.
      invalidateKeys: [['fred'], ['percentiles'], qk.freshness, qk.status],
    };
  }

  if (pathname.startsWith('/private-credit')) {
    return {
      label: 'REFRESH BDC',
      shortLabel: 'BDC',
      metaKey: 'last_bdc_refresh',
      jobId: 'bdc',
      invalidateKeys: [['bdc'], qk.status],
    };
  }

  if (pathname.startsWith('/regulatory')) {
    return {
      label: 'REFRESH REG',
      shortLabel: 'REG',
      metaKey: 'last_regulatory_refresh',
      jobId: 'regulatory',
      invalidateKeys: [['regulatory'], qk.status],
    };
  }

  // ABS/EDGAR + Deals tabs are parked (Deals to be replaced by the cashflows
  // UI, ABS/EDGAR to be reworked) — their refresh specs went with them.

  // Analyst + Watchlists are views over already-stored state. No upstream
  // pull to drive — the user's REFRESH on those pages is the per-panel
  // GENERATE / VIEW buttons that already exist.
  return null;
}

export function useTabRefresh() {
  const queryClient = useQueryClient();
  const { pathname } = useLocation();
  const spec = specFor(pathname);

  const mutation = useMutation({
    mutationFn: async () => {
      if (!spec) throw new Error('No refresh wired for this route');
      return runJobToCompletion(spec.jobId);
    },
    onSuccess: (rows) => {
      if (!spec) return;
      for (const key of spec.invalidateKeys) {
        queryClient.invalidateQueries({ queryKey: [...key] });
      }
      showToast(`${spec.shortLabel} REFRESH DONE · ${rows} ROWS`);
    },
    onError: (err) => {
      if (!spec) return;
      showToast(
        `${spec.shortLabel} REFRESH: ${apiErrorMessage(err, 'failed')}`,
        'error',
      );
    },
  });

  return {
    spec,
    isPending: mutation.isPending,
    isError: mutation.isError,
    error: mutation.error,
    refresh: () => mutation.mutate(),
    errorMessage: mutation.error
      ? apiErrorMessage(mutation.error, 'Refresh failed.')
      : null,
  };
}
