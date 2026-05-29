// EDGAR ABS/structured-finance filings: filterable, paginated table.

import { useState } from 'react';
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { ExternalLink } from 'lucide-react';
import { getEdgarFacets, getEdgarFilings } from '../../lib/api';
import { qk } from '../../lib/queryKeys';
import { fmtDate } from '../../lib/utils';
import type { EdgarFiling } from '../../lib/types';
import Panel from '../shared/Panel';
import LoadingCursor from '../shared/LoadingCursor';
import EmptyState from '../shared/EmptyState';

const PAGE = 50;

const FORM_COLOR: Record<string, string> = {
  'ABS-15G': 'var(--warning)',
  'ABS-EE': 'var(--neutral)',
  '424B5': 'var(--positive)',
  'S-3': 'var(--text-secondary)',
  '8-K': 'var(--cat-fintech)',
};

function formColor(form: string | null): string {
  return FORM_COLOR[form ?? ''] ?? 'var(--text-secondary)';
}

function FilterSelect({
  value,
  onChange,
  allLabel,
  options,
}: {
  value: string;
  onChange: (v: string) => void;
  allLabel: string;
  options: string[];
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
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
      <option value="">{allLabel}</option>
      {options.map((o) => (
        <option key={o} value={o} style={{ background: 'var(--bg-panel)' }}>
          {o}
        </option>
      ))}
    </select>
  );
}

export default function EdgarFeed() {
  const [formType, setFormType] = useState('');
  const [assetClass, setAssetClass] = useState('');
  const [issuanceType, setIssuanceType] = useState<'' | 'debt' | 'equity'>('');

  const { data: facets } = useQuery({
    queryKey: qk.edgarFacets,
    queryFn: () => getEdgarFacets().then((r) => r.data),
    staleTime: 30 * 60_000,
  });

  const params = {
    form_type: formType || undefined,
    asset_class: assetClass || undefined,
    issuance_type: issuanceType || undefined,
  };
  const { data, isLoading, isError, fetchNextPage, hasNextPage, isFetchingNextPage } =
    useInfiniteQuery({
      queryKey: qk.edgarFeed(params),
      initialPageParam: 0,
      queryFn: ({ pageParam }) =>
        getEdgarFilings({ ...params, limit: PAGE, offset: pageParam }).then((r) => r.data),
      getNextPageParam: (last, all) =>
        last.length === PAGE ? all.length * PAGE : undefined,
    });

  const rows: EdgarFiling[] = data?.pages.flat() ?? [];

  const actions = (
    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
      <FilterSelect
        value={formType}
        onChange={setFormType}
        allLabel="ALL FORMS"
        options={facets?.form_types ?? []}
      />
      <FilterSelect
        value={assetClass}
        onChange={setAssetClass}
        allLabel="ALL ASSET CLASSES"
        options={facets?.asset_classes ?? []}
      />
      <FilterSelect
        value={issuanceType}
        onChange={(v) => setIssuanceType(v as '' | 'debt' | 'equity')}
        allLabel="DEBT + EQUITY"
        options={['debt', 'equity']}
      />
    </div>
  );

  return (
    <Panel title="ABS / Structured Finance Filings — EDGAR" actions={actions}>
      {isLoading ? (
        <LoadingCursor />
      ) : isError ? (
        <EmptyState message="FAILED TO LOAD FILINGS" />
      ) : rows.length === 0 ? (
        <EmptyState message="NO FILINGS MATCH THESE FILTERS" />
      ) : (
        <>
          <table className="data-table">
            <thead>
              <tr>
                <th>Filed</th>
                <th>Company</th>
                <th>Form</th>
                <th>Asset Class</th>
                <th>Type</th>
                <th style={{ textAlign: 'center' }}>Link</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((f) => (
                <tr key={f.accession_no}>
                  <td className="dim" style={{ whiteSpace: 'nowrap' }}>{fmtDate(f.filed_at)}</td>
                  <td title={f.company_name ?? ''} style={{ maxWidth: 360, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {f.company_name || '—'}
                  </td>
                  <td>
                    <span className="cat-chip" style={{ color: formColor(f.form_type) }}>
                      {f.form_type}
                    </span>
                  </td>
                  <td>
                    <span className="cat-chip" style={{ color: 'var(--cat-abs)' }}>
                      {f.asset_class}
                    </span>
                  </td>
                  <td>
                    {f.issuance_type && (
                      <span
                        className="cat-chip"
                        style={{
                          color:
                            f.issuance_type === 'debt'
                              ? 'var(--warning)'
                              : 'var(--positive)',
                        }}
                      >
                        {f.issuance_type}
                      </span>
                    )}
                  </td>
                  <td style={{ textAlign: 'center' }}>
                    {f.url && (
                      <a href={f.url} target="_blank" rel="noreferrer" title="Open on SEC.gov">
                        <ExternalLink size={12} />
                      </a>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {hasNextPage && (
            <div style={{ textAlign: 'center', marginTop: 8 }}>
              <button
                className="btn"
                onClick={() => fetchNextPage()}
                disabled={isFetchingNextPage}
                style={{ padding: '5px 16px' }}
              >
                {isFetchingNextPage ? 'LOADING' : 'LOAD MORE'}
              </button>
            </div>
          )}
        </>
      )}
    </Panel>
  );
}
