// Centralized React Query keys so invalidation stays consistent.

export const qk = {
  status: ['status'] as const,
  digests: ['digests'] as const,
  feedHealth: ['feed-health'] as const,
  articles: (params: Record<string, unknown>) => ['articles', params] as const,
  marketSnapshot: ['market', 'snapshot'] as const,
  marketHistory: (ticker: string) => ['market', 'history', ticker] as const,
  percentiles: ['market', 'percentiles'] as const,
  fredLatest: ['fred', 'latest'] as const,
  fredHistory: (seriesId: string) => ['fred', 'history', seriesId] as const,
  forwardCurve: ['fred', 'forward-curve'] as const,
  sofr: ['fred', 'sofr'] as const,
  edgarFilings: (limit: number) => ['edgar', 'filings', limit] as const,
  edgarFeed: (params: Record<string, unknown>) => ['edgar-feed', params] as const,
  edgarFacets: ['edgar', 'facets'] as const,
  absPricing: (segment: string) => ['abs', 'pricing', segment] as const,
  absMomentumDeltas: ['abs', 'momentum-deltas'] as const,
  absNewIssues: (params: Record<string, unknown>) => ['abs', 'new-issues', params] as const,
  absSpreadSeries: (params: Record<string, unknown>) =>
    ['abs', 'spread-series', params] as const,
  absDealSummary: (daysBack: number) => ['abs', 'deal-summary', daysBack] as const,
  deals: ['deals'] as const,
  deal: (id: string, view: string) => ['deals', id, view] as const,

  // Phase 7
  bdcWatchList: ['bdc', 'watch-list'] as const,
  bdcNonaccrualTrend: ['bdc', 'nonaccrual-trend'] as const,
  bdcAggregateTrend: ['bdc', 'aggregate-trend'] as const,
  bdcSummary: (period: string | undefined) => ['bdc', 'summary', period ?? 'latest'] as const,
  bdcLatestPerBdc: ['bdc', 'latest-per-bdc'] as const,
  bdcNonaccruals: (limit: number) => ['bdc', 'nonaccruals', limit] as const,
  regulatoryActions: (params: Record<string, unknown>) =>
    ['regulatory', 'actions', params] as const,
  h8Metrics: ['h8', 'metrics'] as const,
  h8CreditImpulse: ['h8', 'credit-impulse'] as const,
  cloSpreadProxy: ['clo', 'spread-proxy'] as const,
  cloFilings: (limit: number) => ['clo', 'filings', limit] as const,
  kbraPresales: (assetClass: string | undefined) =>
    ['kbra', 'presales', assetClass ?? 'all'] as const,

  // Analyst briefings
  briefings: ['briefings'] as const,
  briefing: (id: string) => ['briefings', id] as const,
  latestBriefing: ['briefings', 'latest'] as const,
};
