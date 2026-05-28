// News page sidebar: min-score dot toggle, category select, source-type chips,
// and the feed-health launcher. All state is lifted to NewsPage.

import { Activity } from 'lucide-react';
import type { SourceType } from '../../lib/types';

export type SourceFilter = Set<SourceType>;

// Order drives the chip layout. Keep publisher rows compact (3 per row at
// typical widths) and the catch-alls at the bottom.
export const SOURCE_OPTIONS: readonly { value: SourceType; label: string }[] = [
  { value: 'bloomberg',   label: 'BLOOMBERG' },
  { value: 'wsj',         label: 'WSJ' },
  { value: 'ft',          label: 'FT' },
  { value: 'marketwatch', label: 'MARKETWATCH' },
  { value: 'cnbc',        label: 'CNBC' },
  { value: 'nyt',         label: 'NYT' },
  { value: 'reuters',     label: 'REUTERS' },
  { value: 'letter',      label: 'LETTERS' },
  { value: 'news',        label: 'OTHER' },
] as const;

export const ALL_SOURCES: SourceFilter = new Set(SOURCE_OPTIONS.map((o) => o.value));

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

function SourceChipButton({
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
        fontSize: 10,
        padding: '3px 6px',
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
  // Keep at least one source active so the user never lands on an empty feed
  // they can't escape from.
  const toggle = (s: SourceType) => {
    const next = new Set(sources);
    if (next.has(s)) next.delete(s);
    else next.add(s);
    if (next.size === 0) return;
    onSources(next);
  };
  const allOn = sources.size === SOURCE_OPTIONS.length;

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
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginBottom: 6,
          }}
        >
          <Label>Source</Label>
          <button
            onClick={() => onSources(new Set(ALL_SOURCES))}
            disabled={allOn}
            className="btn"
            style={{
              fontSize: 9,
              padding: '2px 6px',
              opacity: allOn ? 0.4 : 1,
            }}
          >
            ALL
          </button>
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
          {SOURCE_OPTIONS.map((s) => (
            <SourceChipButton
              key={s.value}
              active={sources.has(s.value)}
              label={s.label}
              onClick={() => toggle(s.value)}
            />
          ))}
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

// Serialise the active source set into a CSV the backend understands.
// Returns undefined when every option is selected so the query stays
// unfiltered (cleaner cache keys, fewer params on the wire).
export function sourceParam(s: SourceFilter): string | undefined {
  if (s.size === 0 || s.size === SOURCE_OPTIONS.length) return undefined;
  return Array.from(s).join(',');
}
