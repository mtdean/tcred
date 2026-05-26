// On-demand AI digest, persisted per day. Browse the archive by date to track
// how the dominant news shifts across the cycle.

import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import axios from 'axios';
import { Moon, Sparkles, Sun } from 'lucide-react';
import { generateDigest, getDigests } from '../../lib/api';
import { qk } from '../../lib/queryKeys';
import { fmtDate, fmtDateTime } from '../../lib/utils';
import type { DigestResponse } from '../../lib/types';
import Panel from '../shared/Panel';
import LoadingCursor from '../shared/LoadingCursor';

function errMessage(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const detail = err.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (err.response?.status) return `Request failed (${err.response.status}).`;
  }
  return 'Failed to generate digest.';
}

const digestKey = (d: DigestResponse) => `${d.date}|${d.session ?? ''}`;

function SessionBadge({ session }: { session: 'AM' | 'PM' }) {
  const isAM = session === 'AM';
  const color = isAM ? 'var(--warning)' : 'var(--neutral)';
  const Icon = isAM ? Sun : Moon;
  return (
    <span
      className="cat-chip"
      style={{ color, display: 'inline-flex', alignItems: 'center', gap: 4 }}
      title={isAM ? 'Morning digest (before noon ET)' : 'Evening digest (noon+ ET)'}
    >
      <Icon size={10} />
      {isAM ? 'AM · MORNING' : 'PM · EVENING'}
    </span>
  );
}

export default function DigestPanel() {
  const queryClient = useQueryClient();
  const [selectedKey, setSelectedKey] = useState<string | null>(null);

  const { data: digests = [], isLoading } = useQuery({
    queryKey: qk.digests,
    queryFn: () => getDigests().then((r) => r.data),
  });

  // Default to the most recent saved digest once history loads.
  useEffect(() => {
    if (!selectedKey && digests.length > 0) setSelectedKey(digestKey(digests[0]));
  }, [digests, selectedKey]);

  const generate = useMutation<DigestResponse, unknown, void>({
    mutationFn: () => generateDigest({ hours_back: 24, min_score: 4 }).then((r) => r.data),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: qk.digests });
      setSelectedKey(digestKey(data));
    },
  });

  const current: DigestResponse | undefined =
    digests.find((d) => digestKey(d) === selectedKey) ?? digests[0];

  const subtitle = current
    ? `${current.date} ${current.session ?? ''} · ${current.article_count} articles · ${current.model}`
    : 'LAST 24H · SCORE ≥ 4';

  const actions = (
    <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
      {digests.length > 0 && (
        <select
          value={selectedKey ?? ''}
          onChange={(e) => setSelectedKey(e.target.value)}
          className="mono"
          title="Browse saved AM/PM digests (US/Eastern)"
          style={{
            background: 'var(--bg-panel-alt)',
            color: 'var(--text-primary)',
            border: '1px solid var(--border-bright)',
            padding: '3px 6px',
            fontSize: 11,
            borderRadius: 2,
          }}
        >
          {digests.map((d) => (
            <option key={digestKey(d)} value={digestKey(d)} style={{ background: 'var(--bg-panel)' }}>
              {fmtDate(d.date)} · {d.session}
            </option>
          ))}
        </select>
      )}
      <button
        className="btn"
        onClick={() => generate.mutate()}
        disabled={generate.isPending}
        title="Generate today's digest (uses Claude tokens)"
        style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
      >
        <Sparkles size={12} />
        {generate.isPending ? 'GENERATING' : 'GENERATE'}
      </button>
    </div>
  );

  return (
    <Panel title="Digest" subtitle={subtitle} actions={actions}>
      {generate.isPending && <span className="loading-cursor muted">SYNTHESIZING DIGEST</span>}

      {!generate.isPending && generate.isError && (
        <div className="muted" style={{ fontSize: 12, marginBottom: 8 }}>
          <span style={{ color: 'var(--warning)' }}>⚠ </span>
          {errMessage(generate.error)}
        </div>
      )}

      {!generate.isPending && current && (
        <div>
          {current.session && (
            <div style={{ marginBottom: 8 }}>
              <SessionBadge session={current.session} />
            </div>
          )}
          <p className="prose" style={{ margin: 0, whiteSpace: 'pre-wrap', color: 'var(--text-primary)' }}>
            {current.summary}
          </p>
          <div className="dim" style={{ fontSize: 10, marginTop: 8 }}>
            covers {fmtDate(current.date_range.from)} – {fmtDate(current.date_range.to)} · generated{' '}
            {fmtDateTime(current.generated_at)}
            {digests.length > 0 && ` · ${digests.length} digest${digests.length > 1 ? 's' : ''} archived`}
          </div>
        </div>
      )}

      {!generate.isPending && !generate.isError && !current && (
        <div className="muted" style={{ fontSize: 12 }}>
          {isLoading ? (
            <LoadingCursor />
          ) : (
            'Press GENERATE to summarize the last 24h of high-relevance news. Digests are saved per AM/PM session (US/Eastern).'
          )}
        </div>
      )}
    </Panel>
  );
}
