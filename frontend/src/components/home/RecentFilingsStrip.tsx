// Horizontal strip of the most recent ABS/EDGAR filings.

import { useQuery } from '@tanstack/react-query';
import { ExternalLink } from 'lucide-react';
import { getEdgarFilings } from '../../lib/api';
import { qk } from '../../lib/queryKeys';
import { fmtDate } from '../../lib/utils';
import type { EdgarFiling } from '../../lib/types';
import Panel from '../shared/Panel';
import LoadingCursor from '../shared/LoadingCursor';
import EmptyState from '../shared/EmptyState';

const FORM_COLOR: Record<string, string> = {
  'ABS-15G': 'var(--warning)',
  'ABS-EE': 'var(--neutral)',
  '424B5': 'var(--positive)',
  'S-3': 'var(--text-secondary)',
};

function Card({ f }: { f: EdgarFiling }) {
  const color = FORM_COLOR[f.form_type ?? ''] ?? 'var(--text-secondary)';
  return (
    <div
      style={{
        border: '1px solid var(--border)',
        background: 'var(--bg-panel-alt)',
        padding: 8,
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span className="cat-chip" style={{ color }}>
          {f.form_type}
        </span>
        <span className="dim" style={{ fontSize: 10 }}>
          {fmtDate(f.filed_at)}
        </span>
      </div>
      <div
        style={{
          fontSize: 12,
          margin: '6px 0 4px',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}
        title={f.company_name ?? ''}
      >
        {f.company_name || '—'}
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span className="muted" style={{ fontSize: 10 }}>
          {f.asset_class}
        </span>
        {f.url && (
          <a href={f.url} target="_blank" rel="noreferrer" title="Open on SEC.gov">
            <ExternalLink size={11} />
          </a>
        )}
      </div>
    </div>
  );
}

export default function RecentFilingsStrip() {
  const { data, isLoading, isError } = useQuery({
    queryKey: qk.edgarFilings(12),
    queryFn: () => getEdgarFilings({ limit: 12 }).then((r) => r.data),
  });

  return (
    <Panel title="Recent ABS Filings">
      {isLoading ? (
        <LoadingCursor />
      ) : isError || !data?.length ? (
        <EmptyState message="NO FILINGS" />
      ) : (
        <div
          style={{
            display: 'grid',
            gap: 8,
            gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
          }}
        >
          {data.map((f) => (
            <Card key={f.accession_no} f={f} />
          ))}
        </div>
      )}
    </Panel>
  );
}
