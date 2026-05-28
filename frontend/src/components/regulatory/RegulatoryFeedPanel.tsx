// Regulatory Pipeline Monitor — Federal Register API + agency RSS feed.
//
// Two buttons:
//   REFRESH  POST /api/regulatory/refresh   (token-free data pull)
//   SCORE    POST /api/regulatory/score     (Claude tokens — manual only)
//
// Unscored rows are surfaced with an "UNSCORED" pill instead of dots so the
// user can preview the queue before spending tokens.

import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ExternalLink, RefreshCw, Sparkles } from 'lucide-react';

import {
  getRegulatoryActions,
  getStatus,
  triggerRegulatoryRefresh,
  triggerRegulatoryScore,
} from '../../lib/api';
import { qk } from '../../lib/queryKeys';
import type { RegulatoryAction } from '../../lib/types';
import { fmtDate, fmtRelative } from '../../lib/utils';
import Panel from '../shared/Panel';
import RangeToggle from '../shared/RangeToggle';
import ScoreDots from '../shared/ScoreDots';
import LoadingCursor from '../shared/LoadingCursor';
import EmptyState from '../shared/EmptyState';

// Agency → accent color. Inline hex so the chip stays readable on either
// theme; mirrors the news SourceChip pattern.
const AGENCY_COLOR: Record<string, string> = {
  CFPB: '#ff9900',  // amber
  OCC:  '#4a90d9',  // blue
  FDIC: '#00c176',  // green
  Fed:  '#e67e22',  // orange
  SEC:  '#9b59b6',  // purple
};

const AGENCIES: readonly string[] = ['CFPB', 'OCC', 'FDIC', 'Fed', 'SEC'] as const;

const ACTION_TYPES = [
  { value: '',              label: 'ALL TYPES' },
  { value: 'RULE',          label: 'RULE' },
  { value: 'PROPOSED_RULE', label: 'PROPOSED RULE' },
  { value: 'NOTICE',        label: 'NOTICE' },
  { value: 'PRESS_RELEASE', label: 'PRESS RELEASE' },
] as const;

const MIN_SCORES = [
  { value: 1, label: 'ALL' },
  { value: 3, label: '3+' },
  { value: 4, label: '4+' },
  { value: 5, label: '5'  },
] as const;

const DAY_RANGES = ['30', '90', '180'] as const;
type DayRange = (typeof DAY_RANGES)[number];

function parseTags(raw: string | null): string[] {
  if (!raw) return [];
  try {
    const v = JSON.parse(raw);
    return Array.isArray(v) ? v.map(String) : [];
  } catch {
    return [];
  }
}

function AgencyChip({ agency }: { agency: string }) {
  const color = AGENCY_COLOR[agency] ?? 'var(--text-secondary)';
  return (
    <span
      className="mono"
      style={{
        color,
        border: `1px solid ${color}`,
        padding: '1px 5px',
        fontSize: 10,
        letterSpacing: 0.5,
        borderRadius: 2,
        whiteSpace: 'nowrap',
      }}
    >
      {agency}
    </span>
  );
}

function UnscoredPill() {
  return (
    <span
      className="mono"
      title="Not yet scored — press SCORE to spend tokens"
      style={{
        color: 'var(--text-dim)',
        border: '1px dashed var(--border-bright)',
        padding: '1px 5px',
        fontSize: 9,
        letterSpacing: 0.5,
        borderRadius: 2,
        whiteSpace: 'nowrap',
      }}
    >
      UNSCORED
    </span>
  );
}

function ActionRow({ a }: { a: RegulatoryAction }) {
  const tags = parseTags(a.relevance_tags);
  const typeLabel = (a.action_type || '').replace(/_/g, ' ');
  const open = () => {
    if (a.html_url) window.open(a.html_url, '_blank', 'noopener,noreferrer');
  };

  return (
    <article
      style={{
        border: '1px solid var(--border)',
        background: 'var(--bg-panel)',
        padding: '8px 10px',
      }}
    >
      <div
        style={{
          display: 'flex',
          gap: 8,
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap', minWidth: 0 }}>
          <AgencyChip agency={a.agency} />
          <span
            className="mono muted"
            style={{ fontSize: 10, letterSpacing: 0.5, textTransform: 'uppercase' }}
          >
            {typeLabel}
          </span>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexShrink: 0 }}>
          {a.relevance_score == null ? (
            <UnscoredPill />
          ) : (
            <ScoreDots score={a.relevance_score} />
          )}
          {a.html_url && (
            <a href={a.html_url} target="_blank" rel="noreferrer" title="Open source">
              <ExternalLink size={12} />
            </a>
          )}
        </div>
      </div>

      <div
        onClick={open}
        style={{
          cursor: a.html_url ? 'pointer' : 'default',
          fontSize: 13,
          marginTop: 5,
          color: 'var(--text-primary)',
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
        }}
        title={a.title}
      >
        {a.title}
      </div>

      {a.abstract && (
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
          {a.abstract}
        </p>
      )}

      <div
        className="mono muted"
        style={{
          fontSize: 10,
          marginTop: 6,
          display: 'flex',
          gap: 14,
          flexWrap: 'wrap',
          letterSpacing: 0.3,
        }}
      >
        <span>PUB {fmtDate(a.publication_date)}</span>
        {a.effective_date && <span>EFF {fmtDate(a.effective_date)}</span>}
        {a.comment_close_date && (
          <span style={{ color: 'var(--warning, var(--accent))' }}>
            COMMENT DUE {fmtDate(a.comment_close_date)}
          </span>
        )}
      </div>

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

export default function RegulatoryFeedPanel() {
  const queryClient = useQueryClient();

  // Filter state. Agencies use a Set so multiple chips can be active; empty
  // set means "no agency filter" (send undefined).
  const [agencies, setAgencies] = useState<Set<string>>(new Set());
  const [actionType, setActionType] = useState<string>('');
  const [minScore, setMinScore] = useState<number>(1);
  const [daysBack, setDaysBack] = useState<DayRange>('90');

  // Backend's agency filter takes a single string. With multiple chips active
  // we fetch unfiltered and narrow client-side so the API call doesn't fan out.
  const queryAgency = agencies.size === 1 ? Array.from(agencies)[0] : undefined;

  const params = useMemo(
    () => ({
      agency: queryAgency,
      action_type: actionType || undefined,
      min_score: minScore,
      days_back: Number(daysBack),
      limit: 200,
    }),
    [queryAgency, actionType, minScore, daysBack],
  );

  const listQ = useQuery({
    queryKey: qk.regulatoryActions(params),
    queryFn: () => getRegulatoryActions(params).then((r) => r.data),
    staleTime: 5 * 60_000,
  });

  // Status drives the "last refreshed" / "last scored" stamps; shares cache
  // with TopBar so no extra request.
  const statusQ = useQuery({
    queryKey: qk.status,
    queryFn: () => getStatus().then((r) => r.data),
    refetchInterval: 30_000,
  });
  const lastRefresh = statusQ.data?.last_regulatory_refresh ?? null;
  const lastScore = statusQ.data?.last_regulatory_score ?? null;

  const refresh = useMutation({
    mutationFn: () => triggerRegulatoryRefresh(7).then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['regulatory', 'actions'] });
      queryClient.invalidateQueries({ queryKey: qk.status });
    },
  });

  const score = useMutation({
    mutationFn: () => triggerRegulatoryScore(100).then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['regulatory', 'actions'] });
      queryClient.invalidateQueries({ queryKey: qk.status });
    },
  });

  const toggleAgency = (a: string) => {
    setAgencies((prev) => {
      const next = new Set(prev);
      if (next.has(a)) next.delete(a);
      else next.add(a);
      return next;
    });
  };

  // Client-side narrow when the multi-select holds >1 agency (backend can't).
  const items: RegulatoryAction[] = useMemo(() => {
    const all = listQ.data ?? [];
    if (agencies.size <= 1) return all;
    return all.filter((a) => agencies.has(a.agency));
  }, [listQ.data, agencies]);

  const actions = (
    <div style={{ display: 'inline-flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
      <span
        className="mono"
        style={{
          fontSize: 10,
          color: lastRefresh ? 'var(--text-secondary)' : 'var(--warning)',
          whiteSpace: 'nowrap',
          letterSpacing: 0.5,
        }}
        title={lastRefresh ?? 'never refreshed'}
      >
        {lastRefresh ? `FETCH ${fmtRelative(lastRefresh).toUpperCase()}` : 'NEVER FETCHED'}
      </span>
      <button
        className="btn"
        onClick={() => refresh.mutate()}
        disabled={refresh.isPending}
        title="Pull recent Federal Register documents + agency press releases (token-free)"
        style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 10 }}
      >
        <RefreshCw
          size={11}
          style={refresh.isPending ? { animation: 'spin 1s linear infinite' } : undefined}
        />
        {refresh.isPending ? 'FETCHING' : 'REFRESH'}
      </button>
      <span
        className="mono"
        style={{
          fontSize: 10,
          color: lastScore ? 'var(--text-secondary)' : 'var(--text-dim)',
          whiteSpace: 'nowrap',
          letterSpacing: 0.5,
        }}
        title={lastScore ?? 'never scored'}
      >
        {lastScore ? `SCORE ${fmtRelative(lastScore).toUpperCase()}` : 'NEVER SCORED'}
      </span>
      <button
        className="btn"
        onClick={() => score.mutate()}
        disabled={score.isPending}
        title="WARNING: spends Claude tokens. Scores up to 100 unscored regulatory actions."
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 4,
          fontSize: 10,
          color: 'var(--text-accent)',
          borderColor: 'var(--text-accent)',
        }}
      >
        <Sparkles
          size={11}
          style={score.isPending ? { animation: 'spin 1s linear infinite' } : undefined}
        />
        {score.isPending ? 'SCORING' : 'SCORE'}
      </button>
    </div>
  );

  return (
    <Panel
      title="Regulatory Pipeline"
      subtitle="FEDERAL REGISTER + AGENCY RSS · CLAUDE SCORING MANUAL"
      actions={actions}
    >
      {/* Filters */}
      <div
        style={{
          display: 'flex',
          gap: 14,
          flexWrap: 'wrap',
          alignItems: 'center',
          marginBottom: 12,
          paddingBottom: 10,
          borderBottom: '1px solid var(--border)',
        }}
      >
        <div style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
          <span
            className="mono muted"
            style={{ fontSize: 10, letterSpacing: 0.5, marginRight: 4 }}
          >
            AGENCY
          </span>
          {AGENCIES.map((a) => {
            const active = agencies.has(a);
            const color = AGENCY_COLOR[a] ?? 'var(--text-secondary)';
            return (
              <button
                key={a}
                className="btn"
                onClick={() => toggleAgency(a)}
                style={{
                  padding: '2px 6px',
                  fontSize: 10,
                  color: active ? color : 'var(--text-secondary)',
                  borderColor: active ? color : 'var(--border-bright)',
                  background: active ? 'var(--bg-selected, transparent)' : 'transparent',
                }}
              >
                {a}
              </button>
            );
          })}
        </div>

        <div style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
          <span
            className="mono muted"
            style={{ fontSize: 10, letterSpacing: 0.5, marginRight: 4 }}
          >
            TYPE
          </span>
          <select
            value={actionType}
            onChange={(e) => setActionType(e.target.value)}
            className="mono"
            style={{
              background: 'var(--bg-panel-alt)',
              color: 'var(--text-primary)',
              border: '1px solid var(--border-bright)',
              padding: '3px 6px',
              fontSize: 11,
              borderRadius: 2,
            }}
          >
            {ACTION_TYPES.map((t) => (
              <option key={t.value} value={t.value} style={{ background: 'var(--bg-panel)' }}>
                {t.label}
              </option>
            ))}
          </select>
        </div>

        <div style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
          <span
            className="mono muted"
            style={{ fontSize: 10, letterSpacing: 0.5, marginRight: 4 }}
          >
            SCORE
          </span>
          <select
            value={minScore}
            onChange={(e) => setMinScore(Number(e.target.value))}
            className="mono"
            style={{
              background: 'var(--bg-panel-alt)',
              color: 'var(--text-primary)',
              border: '1px solid var(--border-bright)',
              padding: '3px 6px',
              fontSize: 11,
              borderRadius: 2,
            }}
          >
            {MIN_SCORES.map((s) => (
              <option key={s.value} value={s.value} style={{ background: 'var(--bg-panel)' }}>
                {s.label}
              </option>
            ))}
          </select>
        </div>

        <div style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
          <span
            className="mono muted"
            style={{ fontSize: 10, letterSpacing: 0.5, marginRight: 4 }}
          >
            DAYS
          </span>
          <RangeToggle
            options={DAY_RANGES}
            value={daysBack}
            onChange={setDaysBack}
          />
        </div>
      </div>

      {/* List */}
      {listQ.isLoading ? (
        <LoadingCursor />
      ) : listQ.isError ? (
        <EmptyState message="FAILED TO LOAD REGULATORY ACTIONS" />
      ) : items.length === 0 ? (
        <EmptyState message="NO ACTIONS MATCH THESE FILTERS — TRY REFRESH" />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {items.map((a) => (
            <ActionRow key={a.id} a={a} />
          ))}
        </div>
      )}
    </Panel>
  );
}
