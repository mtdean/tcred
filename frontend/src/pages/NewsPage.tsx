import { useState } from 'react';
import Panel from '../components/shared/Panel';
import FeedControls, { ALL_SOURCES, SOURCE_OPTIONS, sourceParam } from '../components/news/FeedControls';
import type { SourceFilter } from '../components/news/FeedControls';
import ArticleFeed from '../components/news/ArticleFeed';
import FeedHealthModal from '../components/news/FeedHealthModal';

export default function NewsPage() {
  const [minScore, setMinScore] = useState(3);
  const [category, setCategory] = useState('');
  const [sources, setSources] = useState<SourceFilter>(new Set(ALL_SOURCES));
  const [healthOpen, setHealthOpen] = useState(false);

  // Subtitle: list selected sources, or 'ALL SOURCES' when nothing is filtered.
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
          category={category}
          onCategory={setCategory}
          sources={sources}
          onSources={setSources}
          onOpenHealth={() => setHealthOpen(true)}
        />
      </Panel>

      <Panel title="News Feed" subtitle={`${sourceLabel} · SCORE ≥ ${minScore}`}>
        <ArticleFeed minScore={minScore} category={category} sourceType={sourceParam(sources)} />
      </Panel>

      <FeedHealthModal open={healthOpen} onOpenChange={setHealthOpen} />
    </div>
  );
}
