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
  deals: ['deals'] as const,
  deal: (id: string, view: string) => ['deals', id, view] as const,
};
