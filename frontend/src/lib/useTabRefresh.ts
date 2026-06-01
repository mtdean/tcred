// Route-aware refresh: maps the current pathname to (a) a label for the
// TopBar button, (b) a list of POST endpoints to fire in parallel,
// (c) the meta key on /api/status that records the last successful run for
// this scope, and (d) the React Query keys to invalidate so the UI repaints.
//
// Pages that are pure views over the existing DB (no upstream pull) — Analyst
// briefings, Watchlists, the Issuer pivot — return `null` so the TopBar can
// hide the button.

import { useLocation } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import axios from 'axios';
import {
  triggerAbsNewIssuesRefresh,
  triggerAbsPricingRefresh,
  triggerBdcRefresh,
  triggerEdgarRefresh,
  triggerFredRefresh,
  triggerMarketRefresh,
  triggerRefresh as triggerNewsRefresh,
  triggerRegulatoryRefresh,
} from './api';
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
  // Returns a list of axios promises so we can fire several pulls in parallel
  // (e.g. ABS = EDGAR + pricing + 424B5). The mutation resolves once all do.
  run: () => Promise<unknown[]>;
  invalidateKeys: readonly (readonly unknown[])[];
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
      run: () => Promise.all([triggerNewsRefresh()]),
      invalidateKeys: [
        ['articles'],
        ['articles-feed'],
        ['status'],
        ['feed-health'],
      ],
    };
  }

  if (pathname.startsWith('/markets')) {
    return {
      label: 'REFRESH MARKETS',
      shortLabel: 'MARKETS',
      metaKey: 'last_market_refresh',
      run: () => Promise.all([triggerMarketRefresh()]),
      invalidateKeys: [
        ['market', 'snapshot'],
        ['market', 'percentiles'],
        ['status'],
      ],
    };
  }

  if (pathname.startsWith('/macro')) {
    return {
      label: 'REFRESH MACRO',
      shortLabel: 'MACRO',
      metaKey: 'last_fred_refresh',
      run: () => Promise.all([triggerFredRefresh()]),
      invalidateKeys: [
        ['fred', 'latest'],
        ['fred', 'forward-curve'],
        ['fred', 'sofr'],
        ['freshness'],
        ['status'],
      ],
    };
  }

  if (pathname.startsWith('/private-credit')) {
    return {
      label: 'REFRESH BDC',
      shortLabel: 'BDC',
      metaKey: 'last_bdc_refresh',
      run: () => Promise.all([triggerBdcRefresh()]),
      invalidateKeys: [
        ['bdc', 'watch-list'],
        ['bdc', 'nonaccrual-trend'],
        ['bdc', 'aggregate-trend'],
        ['bdc', 'latest-per-bdc'],
        ['status'],
      ],
    };
  }

  if (pathname.startsWith('/regulatory')) {
    return {
      label: 'REFRESH REG',
      shortLabel: 'REG',
      metaKey: 'last_regulatory_refresh',
      run: () => Promise.all([triggerRegulatoryRefresh()]),
      invalidateKeys: [['regulatory', 'actions'], ['status']],
    };
  }

  // ABS/EDGAR is the umbrella for three different upstream pulls; fire them
  // all so the page is fully refreshed from one tap.
  if (pathname.startsWith('/abs')) {
    return {
      label: 'REFRESH ABS',
      shortLabel: 'ABS',
      metaKey: 'last_abs_424b5_refresh',
      run: () => Promise.all([
        triggerEdgarRefresh(),
        triggerAbsPricingRefresh(),
        triggerAbsNewIssuesRefresh(),
      ]),
      invalidateKeys: [
        ['edgar', 'filings'],
        ['edgar', 'facets'],
        ['edgar-feed'],
        ['abs', 'pricing'],
        ['abs', 'momentum-deltas'],
        ['abs', 'new-issues'],
        ['abs', 'spread-series'],
        ['abs', 'deal-summary'],
        ['status'],
      ],
    };
  }

  if (pathname.startsWith('/deals')) {
    return {
      label: 'REFRESH FILINGS',
      shortLabel: 'FILINGS',
      metaKey: 'last_abs_424b5_refresh',
      run: () => Promise.all([triggerEdgarRefresh()]),
      invalidateKeys: [
        ['issuers'],
        ['edgar', 'filings'],
        ['status'],
      ],
    };
  }

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
      return spec.run();
    },
    onSuccess: () => {
      if (!spec) return;
      for (const key of spec.invalidateKeys) {
        queryClient.invalidateQueries({ queryKey: [...key] });
      }
    },
  });

  return {
    spec,
    isPending: mutation.isPending,
    isError: mutation.isError,
    error: mutation.error,
    refresh: () => mutation.mutate(),
    errorMessage: errorMessage(mutation.error),
  };
}

function errorMessage(err: unknown): string | null {
  if (!err) return null;
  if (axios.isAxiosError(err)) {
    const detail = err.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (err.response?.status) return `Request failed (${err.response.status}).`;
  }
  if (err instanceof Error) return err.message;
  return 'Refresh failed.';
}
