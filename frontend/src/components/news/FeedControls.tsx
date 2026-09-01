// News page sidebar: min-score dot toggle, category chips, source-type chips,
// and the feed-health launcher. All state is lifted to NewsPage.
//
// Chip colors match the chips rendered on each ArticleCard so the same hue
// represents the same publisher / category everywhere on the page.

import { Activity } from 'lucide-react';
import type { SourceType } from '../../lib/types';
import { categoryColor } from '../shared/CategoryChip';
import { sourceTypeColor } from '../../lib/colors';

export type SourceFilter = Set<SourceType>;
export type CategoryFilter = Set<string>;

export const SOURCE_OPTIONS: readonly { value: SourceType; label: string }[] = [
  { value: 'bloomberg',   label: 'BLOOMBERG' },
  { value: 'wsj',         label: 'WSJ' },
  { value: 'ft',          label: 'FT' },
  { value: 'marketwatch', label: 'MARKETWATCH' },
  { value: 'cnbc',        label: 'CNBC' },
  { value: 'nyt',         label: 'NYT' },
  { value: 'reuters',     label: 'REUTERS' },
  { value: 'research',    label: 'RESEARCH' },
  { value: 'restructuring', label: 'DISTRESSED' },
  { value: 'letter',      label: 'LETTERS' },
  { value: 'news',        label: 'OTHER' },
] as const;

export const CATEGORY_OPTIONS: readonly { value: string; label: string }[] = [
  { value: 'macro',              label: 'MACRO' },
  { value: 'credit',             label: 'CREDIT' },
  { value: 'structured_finance', label: 'STRUCTURED' },
  { value: 'fintech',            label: 'FINTECH' },
  { value: 'data_science',       label: 'DATA' },
  // Meco/Gmail-ingested newsletters (data/gmail_ingest.py) without a
  // per-sender category override land here.
  { value: 'newsletter',         label: 'NEWSLETTER' },
] as const;

export const ALL_SOURCES: SourceFilter = new Set(SOURCE_OPTIONS.map((o) => o.value));
export const ALL_CATEGORIES: CategoryFilter = new Set(CATEGORY_OPTIONS.map((o) => o.value));

interface Props {
  minScore: number;
  onMinScore: (n: number) => void;
  categories: CategoryFilter;
  onCategories: (c: CategoryFilter) => void;
  sources: SourceFilter;
  onSources: (s: SourceFilter) => void;
  onOpenHealth: () => void;
}

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

// Color-aware chip. When active, paints the text + border in the brand color
// supplied by the parent; inactive chips fall back to the neutral palette so
// the strip doesn't look like a scattered rainbow.
function FilterChip({
  active,
  label,
  color,
  onClick,
}: {
  active: boolean;
  label: string;
  color: string;
  onClick: () => void;
}) {
  return (
    <button
      className="btn"
      onClick={onClick}
      style={{
        fontSize: 10,
        padding: '3px 6px',
        color: active ? color : 'var(--text-secondary)',
        borderColor: active ? color : 'var(--border-bright)',
        background: active ? 'var(--bg-selected)' : 'var(--bg-panel-alt)',
      }}
    >
      {label}
    </button>
  );
}

function ResetButton({ disabled, onClick }: { disabled: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="btn"
      style={{ fontSize: 9, padding: '2px 6px', opacity: disabled ? 0.4 : 1 }}
    >
      ALL
    </button>
  );
}

export default function FeedControls({
  minScore,
  onMinScore,
  categories,
  onCategories,
  sources,
  onSources,
  onOpenHealth,
}: Props) {
  const toggleSource = (s: SourceType) => {
    const next = new Set(sources);
    if (next.has(s)) next.delete(s);
    else next.add(s);
    if (next.size === 0) return; // keep at least one active
    onSources(next);
  };
  const toggleCategory = (c: string) => {
    const next = new Set(categories);
    if (next.has(c)) next.delete(c);
    else next.add(c);
    if (next.size === 0) return;
    onCategories(next);
  };
  const allSourcesOn = sources.size === SOURCE_OPTIONS.length;
  const allCategoriesOn = categories.size === CATEGORY_OPTIONS.length;

  return (
    <aside style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div>
        <Label>Min Score</Label>
        <ScoreToggle value={minScore} onChange={onMinScore} />
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
          <Label>Category</Label>
          <ResetButton disabled={allCategoriesOn} onClick={() => onCategories(new Set(ALL_CATEGORIES))} />
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
          {CATEGORY_OPTIONS.map((c) => (
            <FilterChip
              key={c.value}
              active={categories.has(c.value)}
              label={c.label}
              color={categoryColor(c.value)}
              onClick={() => toggleCategory(c.value)}
            />
          ))}
        </div>
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
          <ResetButton disabled={allSourcesOn} onClick={() => onSources(new Set(ALL_SOURCES))} />
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
          {SOURCE_OPTIONS.map((s) => (
            <FilterChip
              key={s.value}
              active={sources.has(s.value)}
              label={s.label}
              color={sourceTypeColor(s.value)}
              onClick={() => toggleSource(s.value)}
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

// CSV-serialise the active set for the backend. Returns undefined when every
// option is selected so the default view skips the filter entirely.
function csv<T extends string>(s: Set<T>, total: number): string | undefined {
  if (s.size === 0 || s.size === total) return undefined;
  return Array.from(s).join(',');
}
export const sourceParam = (s: SourceFilter) => csv(s, SOURCE_OPTIONS.length);
export const categoryParam = (c: CategoryFilter) => csv(c, CATEGORY_OPTIONS.length);
