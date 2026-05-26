// News page sidebar: min-score dot toggle, category select, source-type toggles,
// and the feed-health launcher. All state is lifted to NewsPage.

import { Activity } from 'lucide-react';
import type { SourceType } from '../../lib/types';

export interface SourceFilter {
  news: boolean;
  letter: boolean;
}

interface Props {
  minScore: number;
  onMinScore: (n: number) => void;
  category: string;
  onCategory: (c: string) => void;
  sources: SourceFilter;
  onSources: (s: SourceFilter) => void;
  onOpenHealth: () => void;
}

const CATEGORIES = [
  { value: '', label: 'ALL' },
  { value: 'macro', label: 'MACRO' },
  { value: 'credit', label: 'CREDIT' },
  { value: 'fintech', label: 'FINTECH' },
  { value: 'structured_finance', label: 'STRUCTURED FINANCE' },
  { value: 'data_science', label: 'DATA SCIENCE' },
];

function Label({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="muted"
      style={{ fontSize: 10, letterSpacing: '0.1em', textTransform: 'uppercase', marginBottom: 6 }}
    >
      {children}
    </div>
  );
}

function ScoreToggle({ value, onChange }: { value: number; onChange: (n: number) => void }) {
  return (
    <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
      {[1, 2, 3, 4, 5].map((i) => (
        <span
          key={i}
          onClick={() => onChange(i)}
          title={`Min score ${i}`}
          style={{
            width: 14,
            height: 14,
            borderRadius: 2,
            cursor: 'pointer',
            background: i <= value ? 'var(--text-accent)' : 'transparent',
            border: `1px solid ${i <= value ? 'var(--text-accent)' : 'var(--border-bright)'}`,
          }}
        />
      ))}
      <span className="mono" style={{ marginLeft: 4, color: 'var(--text-accent)' }}>
        {value}
      </span>
    </div>
  );
}

function SourceButton({
  active,
  label,
  onClick,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      className="btn"
      onClick={onClick}
      style={{
        flex: 1,
        color: active ? 'var(--text-accent)' : 'var(--text-secondary)',
        borderColor: active ? 'var(--text-accent)' : 'var(--border-bright)',
        background: active ? 'var(--bg-selected)' : 'var(--bg-panel-alt)',
      }}
    >
      {label}
    </button>
  );
}

export default function FeedControls({
  minScore,
  onMinScore,
  category,
  onCategory,
  sources,
  onSources,
  onOpenHealth,
}: Props) {
  // Keep at least one source active.
  const toggle = (k: keyof SourceFilter) => {
    const next = { ...sources, [k]: !sources[k] };
    if (!next.news && !next.letter) return;
    onSources(next);
  };

  return (
    <aside style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div>
        <Label>Min Score</Label>
        <ScoreToggle value={minScore} onChange={onMinScore} />
      </div>

      <div>
        <Label>Category</Label>
        <select
          value={category}
          onChange={(e) => onCategory(e.target.value)}
          className="mono"
          style={{
            width: '100%',
            background: 'var(--bg-panel-alt)',
            color: 'var(--text-primary)',
            border: '1px solid var(--border-bright)',
            padding: '4px 6px',
            fontSize: 12,
            borderRadius: 2,
          }}
        >
          {CATEGORIES.map((c) => (
            <option key={c.value} value={c.value} style={{ background: 'var(--bg-panel)' }}>
              {c.label}
            </option>
          ))}
        </select>
      </div>

      <div>
        <Label>Source Type</Label>
        <div style={{ display: 'flex', gap: 6 }}>
          <SourceButton active={sources.news} label="NEWS" onClick={() => toggle('news')} />
          <SourceButton active={sources.letter} label="LETTERS" onClick={() => toggle('letter')} />
        </div>
      </div>

      <button
        className="btn"
        onClick={onOpenHealth}
        style={{ display: 'inline-flex', alignItems: 'center', gap: 6, justifyContent: 'center' }}
      >
        <Activity size={12} />
        FEED HEALTH
      </button>
    </aside>
  );
}

export function sourceParam(s: SourceFilter): SourceType | undefined {
  if (s.news && !s.letter) return 'news';
  if (s.letter && !s.news) return 'letter';
  return undefined; // both → no filter
}
