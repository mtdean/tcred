// Compact terminal-style article card: chip, title, metadata, snippet, score, tags.
// Clicking the title opens the source in a new tab and marks the article read.

import { ExternalLink } from 'lucide-react';
import type { Article } from '../../lib/types';
import { fmtRelative } from '../../lib/utils';
import CategoryChip from '../shared/CategoryChip';
import SourceChip from '../shared/SourceChip';
import ScoreDots from '../shared/ScoreDots';

interface Props {
  article: Article;
  onRead: (id: string) => void;
}

function parseTags(raw: string | null): string[] {
  if (!raw) return [];
  try {
    const v = JSON.parse(raw);
    return Array.isArray(v) ? v.map(String) : [];
  } catch {
    return [];
  }
}

export default function ArticleCard({ article: a, onRead }: Props) {
  const read = a.is_read === 1;
  const tags = parseTags(a.relevance_tags);

  const open = () => {
    if (!read) onRead(a.id);
    window.open(a.url, '_blank', 'noopener,noreferrer');
  };

  return (
    <article
      style={{
        border: '1px solid var(--border)',
        background: 'var(--bg-panel)',
        padding: '8px 10px',
      }}
    >
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', minWidth: 0, flexWrap: 'wrap' }}>
          <SourceChip feedName={a.feed_name} sourceType={a.source_type} />
          <CategoryChip category={a.feed_category} />
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexShrink: 0 }}>
          <ScoreDots score={a.relevance_score} />
          <a href={a.url} target="_blank" rel="noreferrer" title="Open source" onClick={() => !read && onRead(a.id)}>
            <ExternalLink size={12} />
          </a>
        </div>
      </div>

      <div
        onClick={open}
        style={{
          cursor: 'pointer',
          fontSize: 13,
          marginTop: 5,
          color: read ? 'var(--text-dim)' : 'var(--text-primary)',
        }}
      >
        {a.title}
      </div>

      <div className="muted" style={{ fontSize: 11, marginTop: 3 }}>
        {fmtRelative(a.published_at ?? a.fetched_at)}
      </div>

      {a.snippet && (
        <p
          className="prose muted"
          style={{
            fontSize: 12,
            margin: '5px 0 0',
            display: '-webkit-box',
            WebkitLineClamp: 2,
            WebkitBoxOrient: 'vertical',
            overflow: 'hidden',
          }}
        >
          {a.snippet}
        </p>
      )}

      {tags.length > 0 && (
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 6 }}>
          {tags.map((t) => (
            <span
              key={t}
              className="mono"
              style={{
                fontSize: 9,
                color: 'var(--text-secondary)',
                border: '1px solid var(--border)',
                padding: '0 5px',
                borderRadius: 2,
              }}
            >
              {t}
            </span>
          ))}
        </div>
      )}
    </article>
  );
}
