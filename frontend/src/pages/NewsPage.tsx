import { useState } from 'react';
import Panel from '../components/shared/Panel';
import FeedControls, { sourceParam } from '../components/news/FeedControls';
import type { SourceFilter } from '../components/news/FeedControls';
import ArticleFeed from '../components/news/ArticleFeed';
import FeedHealthModal from '../components/news/FeedHealthModal';

export default function NewsPage() {
  const [minScore, setMinScore] = useState(3);
  const [category, setCategory] = useState('');
  const [sources, setSources] = useState<SourceFilter>({ news: true, letter: true });
  const [healthOpen, setHealthOpen] = useState(false);

  const sourceLabel = sources.news && sources.letter ? 'NEWS + LETTERS' : sources.news ? 'NEWS' : 'LETTERS';

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
