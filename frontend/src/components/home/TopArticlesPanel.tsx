// Top scored stories for a given category. Reused for CREDIT and MACRO columns.

import { useQuery } from '@tanstack/react-query';
import { getArticles } from '../../lib/api';
import { qk } from '../../lib/queryKeys';
import { fmtRelative } from '../../lib/utils';
import type { Article } from '../../lib/types';
import Panel from '../shared/Panel';
import LoadingCursor from '../shared/LoadingCursor';
import EmptyState from '../shared/EmptyState';
import ScoreDots from '../shared/ScoreDots';

interface Props {
  title: string;
  // Filter by topic category, source-type slug, or both. At least one is
  // expected; with neither this is just the top-scored stories overall.
  category?: string;
  sourceType?: string;
  minScore?: number;
  limit?: number;
}

function Row({ a }: { a: Article }) {
  return (
    <li style={{ padding: '6px 0', borderBottom: '1px solid var(--border)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
        <a href={a.url} target="_blank" rel="noreferrer" style={{ color: 'var(--text-primary)' }}>
          {a.title}
        </a>
        <span style={{ flexShrink: 0 }}>
          <ScoreDots score={a.relevance_score} />
        </span>
      </div>
      <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>
        {a.feed_name} · {fmtRelative(a.published_at ?? a.fetched_at)}
      </div>
    </li>
  );
}

export default function TopArticlesPanel({
  title,
  category,
  sourceType,
  minScore = 4,
  limit = 5,
}: Props) {
  const params = { category, source_type: sourceType, min_score: minScore, limit };
  const { data, isLoading, isError } = useQuery({
    queryKey: qk.articles(params),
    queryFn: () => getArticles(params).then((r) => r.data),
  });

  const items = data?.items ?? [];

  return (
    <Panel title={title}>
      {isLoading ? (
        <LoadingCursor />
      ) : isError ? (
        <EmptyState message="FAILED TO LOAD" />
      ) : items.length === 0 ? (
        <EmptyState message="NO SCORED STORIES — SET ANTHROPIC_API_KEY AND REFRESH" />
      ) : (
        <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
          {items.map((a) => (
            <Row key={a.id} a={a} />
          ))}
        </ul>
      )}
    </Panel>
  );
}
