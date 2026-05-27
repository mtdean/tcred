// ABS new-issue spread momentum: recent deals priced at issue, with per-tranche
// spreads. Subordinate subprime-auto spreads are the consumer-risk-premium read.

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ExternalLink } from 'lucide-react';
import { getAbsPricing } from '../../lib/api';
import { qk } from '../../lib/queryKeys';
import { fmtDate } from '../../lib/utils';
import type { AbsDeal, AbsTranche } from '../../lib/types';
import Panel from '../shared/Panel';
import LoadingCursor from '../shared/LoadingCursor';
import EmptyState from '../shared/EmptyState';

const SEGMENTS = [
  { value: '', label: 'ALL SEGMENTS' },
  { value: 'subprime_auto', label: 'SUBPRIME AUTO' },
  { value: 'prime_auto', label: 'PRIME AUTO' },
  { value: 'equipment', label: 'EQUIPMENT' },
];

const SEGMENT_LABEL: Record<string, string> = {
  subprime_auto: 'SUBPRIME',
  prime_auto: 'PRIME',
  equipment: 'EQUIP',
  other: 'OTHER',
};

function segmentColor(seg: string): string {
  if (seg === 'subprime_auto') return 'var(--negative)';
  if (seg === 'prime_auto') return 'var(--positive)';
  if (seg === 'equipment') return 'var(--neutral)';
  return 'var(--text-secondary)';
}

// Senior A-tranches read tight; mezz (B/C) and junior (D+) carry the risk signal.
function trancheColor(cls: string): string {
  const c = cls.toUpperCase();
  if (c.startsWith('A')) return 'var(--text-secondary)';
  if (c.startsWith('B')) return 'var(--text-primary)';
  if (c.startsWith('C')) return 'var(--warning)';
  return 'var(--negative)'; // D, E, N, ...
}

function TrancheChips({ tranches }: { tranches: AbsTranche[] }) {
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
      {tranches.map((t) => (
        <span
          key={t.class_name}
          className="mono"
          title={[
            t.rating && `rating ${t.rating}`,
            t.wal != null && `WAL ${t.wal}y`,
            t.benchmark && `vs ${t.benchmark}`,
            t.coupon != null && `coupon ${t.coupon}%`,
          ]
            .filter(Boolean)
            .join(' · ')}
          style={{
            fontSize: 10,
            padding: '1px 5px',
            border: '1px solid var(--border-bright)',
            borderRadius: 2,
            color: trancheColor(t.class_name),
            whiteSpace: 'nowrap',
          }}
        >
          {t.class_name} <b>+{t.spread_bps != null ? Math.round(t.spread_bps) : '—'}</b>
        </span>
      ))}
    </div>
  );
}

export default function AbsPricingPanel() {
  const [segment, setSegment] = useState('');

  const { data, isLoading, isError } = useQuery({
    queryKey: qk.absPricing(segment),
    queryFn: () => getAbsPricing(segment).then((r) => r.data),
    staleTime: 30 * 60_000,
  });

  const deals: AbsDeal[] = data ?? [];

  const actions = (
    <select
      value={segment}
      onChange={(e) => setSegment(e.target.value)}
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
      {SEGMENTS.map((s) => (
        <option key={s.value} value={s.value} style={{ background: 'var(--bg-panel)' }}>
          {s.label}
        </option>
      ))}
    </select>
  );

  return (
    <Panel
      title="ABS New-Issue Spreads"
      subtitle="bps OVER BENCHMARK, AT PRICING"
      actions={actions}
    >
      {isLoading ? (
        <LoadingCursor />
      ) : isError ? (
        <EmptyState message="FAILED TO LOAD ABS PRICING" />
      ) : deals.length === 0 ? (
        <EmptyState message="NO PRICED DEALS YET — RUN POST /api/abs/pricing/refresh" />
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Priced</th>
              <th>Deal</th>
              <th>Segment</th>
              <th>Tranche Spreads</th>
              <th style={{ textAlign: 'center' }}>Link</th>
            </tr>
          </thead>
          <tbody>
            {deals.map((d) => (
              <tr key={d.accession_no}>
                <td className="dim" style={{ whiteSpace: 'nowrap' }}>{fmtDate(d.pricing_date)}</td>
                <td title={d.deal_name} style={{ maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {d.deal_name}
                </td>
                <td>
                  <span className="cat-chip" style={{ color: segmentColor(d.segment) }}>
                    {SEGMENT_LABEL[d.segment] ?? d.segment}
                  </span>
                </td>
                <td><TrancheChips tranches={d.tranches} /></td>
                <td style={{ textAlign: 'center' }}>
                  {d.url && (
                    <a href={d.url} target="_blank" rel="noreferrer" title="Open term sheet on SEC.gov">
                      <ExternalLink size={12} />
                    </a>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}
