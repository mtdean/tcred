import { useState } from 'react';
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
import FeedHealthModal from '../components/news/FeedHealthModal';

export default function NewsPage() {
  const [minScore, setMinScore] = useState(3);
  const [categories, setCategories] = useState<CategoryFilter>(new Set(ALL_CATEGORIES));
  const [sources, setSources] = useState<SourceFilter>(new Set(ALL_SOURCES));
  const [healthOpen, setHealthOpen] = useState(false);

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
    <div style={{ display: 'grid', gridTemplateColumns: '200px 1fr', gap: 12, alignItems: 'start' }}>
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
        title="News Feed"
        subtitle={`${categoryLabel} · ${sourceLabel} · SCORE ≥ ${minScore}`}
      >
        <ArticleFeed
          minScore={minScore}
          category={categoryParam(categories)}
          sourceType={sourceParam(sources)}
        />
      </Panel>

      <FeedHealthModal open={healthOpen} onOpenChange={setHealthOpen} />
    </div>
  );
}
