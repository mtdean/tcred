// KBRA Presale Parser — manual-only Claude extraction. User drops PDFs into
// backend/data/kbra/presales/, then presses PARSE to fire POST /api/kbra/refresh.
// Server-side scanner (backend/data/kbra.py) is the only consumer of that endpoint.
//
// Data path: GET /api/kbra/presales (optional asset_class filter).
//            POST /api/kbra/refresh  (token-spending; only ever triggered here).

import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { RefreshCw } from 'lucide-react';

import { getKbraPresales, getStatus, triggerKbraRefresh } from '../../lib/api';
import { qk } from '../../lib/queryKeys';
import type { KbraPresale } from '../../lib/types';
import { fmtDate, fmtRelative } from '../../lib/utils';
import Panel from '../shared/Panel';
import EmptyState from '../shared/EmptyState';
import LoadingCursor from '../shared/LoadingCursor';

// Mirror the taxonomy used elsewhere in the ABS pages so the filter values match
// whatever Claude returns for asset_class (it's prompted with these exact strings).
const ASSET_CLASSES: { value: string; label: string }[] = [
  { value: '',                    label: 'ALL ASSET CLASSES' },
  { value: 'prime_auto_loan',     label: 'PRIME AUTO LOAN' },
  { value: 'subprime_auto_loan',  label: 'SUBPRIME AUTO LOAN' },
  { value: 'auto_lease',          label: 'AUTO LEASE' },
  { value: 'credit_card',         label: 'CREDIT CARD' },
  { value: 'equipment',           label: 'EQUIPMENT' },
  { value: 'consumer_loan',       label: 'CONSUMER LOAN' },
  { value: 'student_loan',        label: 'STUDENT LOAN' },
  { value: 'solar',               label: 'SOLAR' },
  { value: 'rmbs_non_agency',     label: 'RMBS (NON-AGENCY)' },
  { value: 'cmbs',                label: 'CMBS' },
  { value: 'clo',                 label: 'CLO' },
];

// KBRA reports CDR/CPR/loss/CE as percent-already (e.g. 5.5 means 5.5%). Some
// extractions occasionally come back as fractions (0.055). Detect by magnitude:
// any value < 1.0 is almost certainly a fraction (a 1% CE would be implausible
// even for a senior AAA in a thick stack), so multiply by 100.
function pctCell(value: number | null | undefined, digits = 1): string {
  if (value == null || Number.isNaN(value)) return '—';
  const scaled = Math.abs(value) > 0 && Math.abs(value) < 1 ? value * 100 : value;
  return `${scaled.toFixed(digits)}%`;
}

function confidenceChipStyle(c: KbraPresale['parse_confidence']): React.CSSProperties {
  if (c === 'high') {
    return { color: 'var(--positive)', borderColor: 'var(--positive)' };
  }
  if (c === 'low') {
    return { color: 'var(--warning)', borderColor: 'var(--warning)' };
  }
  // 'medium' or null → neutral
  return { color: 'var(--text-secondary)', borderColor: 'var(--border-bright)' };
}

export default function KbraPresalesPanel() {
  // Empty string == ALL. Stored as the literal string so it round-trips cleanly
  // through the <select>'s value attribute.
  const [assetClass, setAssetClass] = useState<string>('');
  const queryClient = useQueryClient();

  const filterValue = assetClass || undefined;

  const statusQ = useQuery({
    queryKey: qk.status,
    queryFn: () => getStatus().then((r) => r.data),
    refetchInterval: 30_000,
  });
  const lastRefresh = statusQ.data?.last_kbra_refresh ?? null;

  const presalesQ = useQuery({
    queryKey: qk.kbraPresales(filterValue),
    queryFn: () =>
      getKbraPresales(filterValue ? { asset_class: filterValue } : {}).then((r) => r.data),
    staleTime: 5 * 60_000,
  });

  // PARSE: invalidate the ALL-rows cache and status, regardless of the current
  // filter — newly-parsed rows might land outside the current asset_class view.
  const refresh = useMutation({
    mutationFn: () => triggerKbraRefresh().then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: qk.kbraPresales(undefined) });
      // Also invalidate the currently-active filter so the table refreshes in place.
      queryClient.invalidateQueries({ queryKey: ['kbra', 'presales'] });
      queryClient.invalidateQueries({ queryKey: qk.status });
    },
  });

  const rows = presalesQ.data ?? [];

  // Count + relative time for the subtitle.
  const subtitle = useMemo(() => {
    const n = rows.length;
    const when = lastRefresh ? fmtRelative(lastRefresh) : 'never refreshed';
    return `${n} PARSED · LAST PARSE ${when.toUpperCase()}`;
  }, [rows.length, lastRefresh]);

  const actions = (
    <div style={{ display: 'inline-flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
      <select
        value={assetClass}
        onChange={(e) => setAssetClass(e.target.value)}
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
        {ASSET_CLASSES.map((c) => (
          <option key={c.value || 'all'} value={c.value} style={{ background: 'var(--bg-panel)' }}>
            {c.label}
          </option>
        ))}
      </select>
      <button
        className="btn"
        onClick={() => refresh.mutate()}
        disabled={refresh.isPending}
        title="Sends presale PDF text to Claude — token-spending"
        style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 10 }}
      >
        <RefreshCw
          size={11}
          style={refresh.isPending ? { animation: 'spin 1s linear infinite' } : undefined}
        />
        {refresh.isPending ? 'PARSING' : 'PARSE'}
      </button>
    </div>
  );

  return (
    <Panel title="KBRA Presales" subtitle={subtitle} actions={actions}>
      <div
        className="muted"
        style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 10, lineHeight: 1.4 }}
      >
        Drop KBRA presale PDFs into <code className="mono">backend/data/kbra/presales/</code>,
        then press PARSE to extract structured assumptions via Claude. The button is the only
        trigger — there is no scheduled job.
      </div>

      {presalesQ.isLoading ? (
        <LoadingCursor />
      ) : presalesQ.isError ? (
        <EmptyState message="FAILED TO LOAD KBRA PRESALES" />
      ) : rows.length === 0 ? (
        <EmptyState message="NO PRESALES PARSED YET — DROP PDFS INTO backend/data/kbra/presales/ AND PRESS PARSE" />
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Deal</th>
              <th>Issuer</th>
              <th>Asset Class</th>
              <th>Closing</th>
              <th style={{ textAlign: 'right' }}>CDR %</th>
              <th style={{ textAlign: 'right' }}>CPR %</th>
              <th style={{ textAlign: 'right' }}>Loss %</th>
              <th style={{ textAlign: 'right' }}>CE AAA %</th>
              <th style={{ textAlign: 'right' }}>CE AA %</th>
              <th style={{ textAlign: 'right' }}>CE A %</th>
              <th style={{ textAlign: 'right' }}>CE BBB %</th>
              <th style={{ textAlign: 'right' }}>CE BB %</th>
              <th>Conf</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td
                  title={r.deal_name ?? r.pdf_filename ?? ''}
                  style={{
                    maxWidth: 220,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {r.deal_name ?? r.pdf_filename ?? '—'}
                </td>
                <td
                  title={r.issuer ?? ''}
                  style={{
                    maxWidth: 180,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {r.issuer ?? '—'}
                </td>
                <td className="mono" style={{ fontSize: 10, letterSpacing: 0.3 }}>
                  {r.asset_class ? r.asset_class.toUpperCase() : '—'}
                </td>
                <td className="dim" style={{ whiteSpace: 'nowrap' }}>
                  {r.closing_date ? fmtDate(r.closing_date) : '—'}
                </td>
                <td className="mono" style={{ textAlign: 'right' }}>{pctCell(r.base_cdr)}</td>
                <td className="mono" style={{ textAlign: 'right' }}>{pctCell(r.base_cpr)}</td>
                <td className="mono" style={{ textAlign: 'right' }}>{pctCell(r.base_loss_rate)}</td>
                <td className="mono" style={{ textAlign: 'right' }}>{pctCell(r.ce_aaa)}</td>
                <td className="mono" style={{ textAlign: 'right' }}>{pctCell(r.ce_aa)}</td>
                <td className="mono" style={{ textAlign: 'right' }}>{pctCell(r.ce_a)}</td>
                <td className="mono" style={{ textAlign: 'right' }}>{pctCell(r.ce_bbb)}</td>
                <td className="mono" style={{ textAlign: 'right' }}>{pctCell(r.ce_bb)}</td>
                <td>
                  <span
                    className="mono"
                    title={`parse confidence: ${r.parse_confidence ?? 'unknown'}`}
                    style={{
                      fontSize: 9,
                      padding: '1px 4px',
                      border: '1px solid',
                      borderRadius: 2,
                      letterSpacing: 0.5,
                      ...confidenceChipStyle(r.parse_confidence),
                    }}
                  >
                    {(r.parse_confidence ?? '—').toUpperCase()}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}
