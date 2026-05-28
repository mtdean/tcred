// Paginated article feed. Infinite-scroll via a LOAD MORE button.
// Marking an article read is optimistic against the cached pages.

import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getArticles, markRead } from '../../lib/api';
import type { ArticleListResponse } from '../../lib/types';
import ArticleCard from './ArticleCard';
import LoadingCursor from '../shared/LoadingCursor';
import EmptyState from '../shared/EmptyState';

const PAGE = 50;

interface Props {
  minScore: number;
  // CSV of category slugs (e.g. 'macro,credit'). Omitted = all categories.
  category?: string;
  // CSV of source-type slugs (e.g. 'wsj,ft,bloomberg'). Omitted = all sources.
  sourceType?: string;
}

type FeedData = { pages: ArticleListResponse[]; pageParams: number[] };

export default function ArticleFeed({ minScore, category, sourceType }: Props) {
  const queryClient = useQueryClient();
  const key = ['articles-feed', { minScore, category, sourceType }] as const;

  const { data, isLoading, isError, fetchNextPage, hasNextPage, isFetchingNextPage } =
    useInfiniteQuery({
      queryKey: key,
      initialPageParam: 0,
      queryFn: ({ pageParam }) =>
        getArticles({
          min_score: minScore,
          category,
          source_type: sourceType,
          limit: PAGE,
          offset: pageParam,
        }).then((r) => r.data),
      getNextPageParam: (last) =>
        last.items.length === last.limit ? last.offset + last.limit : undefined,
    });

  const read = useMutation({
    mutationFn: (id: string) => markRead(id),
    onMutate: async (id: string) => {
      // Optimistically flip is_read across all cached feed pages.
      queryClient.setQueriesData<FeedData>({ queryKey: ['articles-feed'] }, (old) => {
        if (!old) return old;
        return {
          ...old,
          pages: old.pages.map((p) => ({
            ...p,
            items: p.items.map((a) => (a.id === id ? { ...a, is_read: 1 } : a)),
          })),
        };
      });
    },
  });

  if (isLoading) return <LoadingCursor />;
  if (isError) return <EmptyState message="FAILED TO LOAD FEED" />;

  const items = data?.pages.flatMap((p) => p.items) ?? [];
  if (items.length === 0) {
    return <EmptyState message="NO ARTICLES MATCH THESE FILTERS" />;
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {items.map((a) => (
        <ArticleCard key={a.id} article={a} onRead={(id) => read.mutate(id)} />
      ))}

      {hasNextPage && (
        <button
          className="btn"
          onClick={() => fetchNextPage()}
          disabled={isFetchingNextPage}
          style={{ alignSelf: 'center', marginTop: 4, padding: '5px 16px' }}
        >
          {isFetchingNextPage ? 'LOADING' : 'LOAD MORE'}
        </button>
      )}
    </div>
  );
}
