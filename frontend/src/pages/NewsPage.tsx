import { useEffect, useState } from 'react';
import { Search, X } from 'lucide-react';
import Panel from '../components/shared/Panel';
import FeedControls, {
  ALL_CATEGORIES,
  ALL_SOURCES,
  CATEGORY_OPTIONS,
  SOURCE_OPTIONS,
  categoryParam,
  sourceParam,
} from '../components/news/FeedControls';
import type { CategoryFilter, SourceFilter } from '../components/news/FeedControls';
import ArticleFeed from '../components/news/ArticleFeed';
import ArticleSearchResults from '../components/news/ArticleSearchResults';
import ArticleReaderModal from '../components/news/ArticleReaderModal';
import FeedHealthModal from '../components/news/FeedHealthModal';

export default function NewsPage() {
  const [minScore, setMinScore] = useState(3);
  const [categories, setCategories] = useState<CategoryFilter>(new Set(ALL_CATEGORIES));
  const [sources, setSources] = useState<SourceFilter>(new Set(ALL_SOURCES));
  const [healthOpen, setHealthOpen] = useState(false);
  const [readerId, setReaderId] = useState<string | null>(null);

  // Search box drives archive search (FTS) when non-empty; debounced so we
  // don't fire a query per keystroke.
  const [searchInput, setSearchInput] = useState('');
  const [query, setQuery] = useState('');
  useEffect(() => {
    const t = setTimeout(() => setQuery(searchInput.trim()), 300);
    return () => clearTimeout(t);
  }, [searchInput]);
  const searching = query.length >= 2;

  // Subtitle pieces — collapse to "ALL ..." when nothing is filtered out.
  const categoryLabel =
    categories.size === CATEGORY_OPTIONS.length
      ? 'ALL CATEGORIES'
      : Array.from(categories)
          .map((c) => CATEGORY_OPTIONS.find((o) => o.value === c)?.label ?? c.toUpperCase())
          .join(' · ');
  const sourceLabel =
    sources.size === SOURCE_OPTIONS.length
      ? 'ALL SOURCES'
      : Array.from(sources)
          .map((s) => SOURCE_OPTIONS.find((o) => o.value === s)?.label ?? s.toUpperCase())
          .join(' · ');

  return (
    <div className="sidebar-grid sidebar-grid--narrow">
      <Panel title="Filters">
        <FeedControls
          minScore={minScore}
          onMinScore={setMinScore}
          categories={categories}
          onCategories={setCategories}
          sources={sources}
          onSources={setSources}
          onOpenHealth={() => setHealthOpen(true)}
        />
      </Panel>

      <Panel
        title={searching ? 'Archive Search' : 'News Feed'}
        subtitle={
          searching
            ? `FULL-TEXT · LAST 365 DAYS · RANKED`
            : `${categoryLabel} · ${sourceLabel} · SCORE ≥ ${minScore}`
        }
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 10 }}>
          <Search size={13} style={{ color: 'var(--text-secondary)', flexShrink: 0 }} />
          <input
            className="mono"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="SEARCH ARCHIVE — titles, snippets, full text…"
            spellCheck={false}
            style={{
              flex: 1,
              minWidth: 0,
              background: 'var(--bg-base, transparent)',
              border: '1px solid var(--border)',
              color: 'var(--text-primary)',
              fontSize: 12,
              padding: '5px 8px',
              outline: 'none',
            }}
          />
          {searchInput && (
            <button
              className="btn"
              style={{ padding: '3px 6px' }}
              onClick={() => setSearchInput('')}
              aria-label="Clear search"
            >
              <X size={12} />
            </button>
          )}
        </div>

        {searching ? (
          <ArticleSearchResults query={query} onOpenReader={setReaderId} />
        ) : (
          <ArticleFeed
            minScore={minScore}
            category={categoryParam(categories)}
            sourceType={sourceParam(sources)}
            onOpenReader={setReaderId}
          />
        )}
      </Panel>

      <ArticleReaderModal articleId={readerId} onClose={() => setReaderId(null)} />
      <FeedHealthModal open={healthOpen} onOpenChange={setHealthOpen} />
    </div>
  );
}
