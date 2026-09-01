// FTS search results: same ArticleCards as the feed, with the BM25 match
// snippet (hit terms wrapped in []) shown under each card.

import { useQuery } from '@tanstack/react-query';
import { markRead, searchArticles } from '../../lib/api';
import ArticleCard from './ArticleCard';
import LoadingCursor from '../shared/LoadingCursor';
import EmptyState from '../shared/EmptyState';

interface Props {
  query: string;
  onOpenReader?: (id: string) => void;
}

export default function ArticleSearchResults({ query, onOpenReader }: Props) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['article-search', query],
    queryFn: () => searchArticles(query).then((r) => r.data),
    staleTime: 60_000,
  });

  if (isLoading) return <LoadingCursor />;
  if (isError) return <EmptyState message="SEARCH FAILED" />;

  const items = data?.items ?? [];
  if (items.length === 0) {
    return <EmptyState message={`NO MATCHES FOR "${query.toUpperCase()}"`} />;
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {items.map((a) => (
        <div key={a.id}>
          <ArticleCard
            article={a}
            onRead={(id) => markRead(id)}
            onOpenReader={onOpenReader}
          />
          <div
            className="mono muted"
            style={{
              fontSize: 11,
              padding: '3px 10px 0',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
            title={a.match_snippet}
          >
            ▸ {a.match_snippet}
          </div>
        </div>
      ))}
    </div>
  );
}
