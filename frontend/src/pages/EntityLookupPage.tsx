// Entity Lookup — cross-source view of one company: BDC holdings (marks
// over time, who holds it now), EDGAR filings, and news. Backed by
// GET /api/entities/lookup?name=; the query lives in the URL (?q=) so a
// lookup is shareable/bookmarkable.

import { useState, type FormEvent } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Search } from 'lucide-react';

import { lookupEntity } from '../lib/api';
import { currency } from '../lib/utils';
import Panel from '../components/shared/Panel';
import LoadingCursor from '../components/shared/LoadingCursor';
import EmptyState from '../components/shared/EmptyState';

function fmtPeriod(period: string | null | undefined): string {
  if (!period) return '—';
  const m = /^(\d{4})-(\d{2})-\d{2}$/.exec(String(period));
  if (!m) return String(period);
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return `${months[Number(m[2]) - 1] ?? m[2]} ${m[1]}`;
}

function markColor(mark: number | null | undefined): string {
  if (mark == null) return 'var(--text-secondary)';
  if (mark < 0.8) return 'var(--negative)';
  if (mark < 0.95) return 'var(--warning)';
  return 'var(--text-primary)';
}

function mm(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—';
  return currency(v / 1e6, 1);
}

export default function EntityLookupPage() {
  const [params, setParams] = useSearchParams();
  const submitted = params.get('q') ?? '';
  const [input, setInput] = useState(submitted);

  const q = useQuery({
    queryKey: ['entity', 'lookup', submitted],
    queryFn: () => lookupEntity(submitted).then((r) => r.data),
    enabled: submitted.trim().length >= 3,
    staleTime: 30 * 60_000,
  });

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    const name = input.trim();
    if (name.length >= 3) setParams({ q: name });
  };

  const d = q.data;
  const hasAny =
    d && !('error' in d && d.error) &&
    (d.holdings_by_period.length > 0 || d.filings.length > 0 || d.articles.length > 0);

  return (
    <div className="stack">
      <Panel
        title="Entity Lookup"
        subtitle="BDC HOLDINGS · EDGAR FILINGS · NEWS · ONE COMPANY AT A TIME"
      >
        <form onSubmit={onSubmit} style={{ display: 'flex', gap: 8 }}>
          <input
            className="mono"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="company name, e.g. Pluralsight"
            style={{
              flex: 1,
              maxWidth: 420,
              background: 'var(--bg-input, #181818)',
              border: '1px solid var(--border-bright)',
              color: 'var(--text-primary)',
              padding: '6px 10px',
              fontSize: 12,
            }}
          />
          <button
            type="submit"
            className="btn"
            disabled={input.trim().length < 3}
            style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}
          >
            <Search size={12} /> LOOKUP
          </button>
        </form>

        {submitted && q.isLoading && <div style={{ marginTop: 12 }}><LoadingCursor /></div>}
        {submitted && q.isError && (
          <div style={{ marginTop: 12 }}>
            <EmptyState message="LOOKUP FAILED — IS THE BACKEND UP?" />
          </div>
        )}
        {d && !q.isLoading && !hasAny && (
          <div style={{ marginTop: 12 }}>
            <EmptyState message={`NOTHING FOUND FOR “${d.query}”`} />
          </div>
        )}
      </Panel>

      {d && hasAny && (
        <>
          {d.holdings_by_period.length > 0 && (
            <Panel
              title="BDC Exposure Over Time"
              subtitle={`AGGREGATED ACROSS MATCHING HOLDINGS${d.truncated_holdings ? ' · TRUNCATED' : ''}`}
            >
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Period</th>
                    <th style={{ textAlign: 'right' }}>BDCs</th>
                    <th style={{ textAlign: 'right' }}>Tranches</th>
                    <th style={{ textAlign: 'right' }}>Cost ($mm)</th>
                    <th style={{ textAlign: 'right' }}>FV ($mm)</th>
                    <th style={{ textAlign: 'right' }}>Mark</th>
                    <th>Flags</th>
                  </tr>
                </thead>
                <tbody>
                  {[...d.holdings_by_period].reverse().map((p) => (
                    <tr key={p.period}>
                      <td className="mono">{fmtPeriod(p.period)}</td>
                      <td className="mono" style={{ textAlign: 'right' }}>{p.n_bdcs}</td>
                      <td className="mono dim" style={{ textAlign: 'right' }}>{p.n_tranches}</td>
                      <td className="mono" style={{ textAlign: 'right' }}>{mm(p.cost_basis)}</td>
                      <td className="mono" style={{ textAlign: 'right' }}>{mm(p.fair_value)}</td>
                      <td className="mono" style={{ textAlign: 'right', color: markColor(p.mark_to_cost) }}>
                        {p.mark_to_cost != null ? p.mark_to_cost.toFixed(3) : '—'}
                      </td>
                      <td className="mono" style={{ color: 'var(--negative)', fontSize: 10 }}>
                        {p.any_nonaccrual ? 'NON-ACCRUAL' : ''}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Panel>
          )}

          {d.current_holders.length > 0 && (
            <Panel
              title="Current Holders"
              subtitle={`AS OF ${fmtPeriod(d.latest_period).toUpperCase()} · ${d.current_holders.length} TRANCHES`}
            >
              <table className="data-table">
                <thead>
                  <tr>
                    <th>BDC</th>
                    <th>Type</th>
                    <th style={{ textAlign: 'right' }}>Rate</th>
                    <th style={{ textAlign: 'right' }}>Cost ($mm)</th>
                    <th style={{ textAlign: 'right' }}>FV ($mm)</th>
                    <th style={{ textAlign: 'right' }}>Mark</th>
                  </tr>
                </thead>
                <tbody>
                  {d.current_holders.slice(0, 30).map((h, i) => (
                    <tr key={i}>
                      <td
                        title={h.bdc_name}
                        style={{ maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                      >
                        {h.bdc_name}
                      </td>
                      <td className="dim" style={{ maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {h.investment_type || '—'}
                      </td>
                      <td className="mono" style={{ textAlign: 'right' }}>
                        {h.interest_rate != null ? `${(h.interest_rate * 100).toFixed(2)}%` : '—'}
                      </td>
                      <td className="mono" style={{ textAlign: 'right' }}>{mm(h.cost_basis)}</td>
                      <td className="mono" style={{ textAlign: 'right' }}>{mm(h.fair_value)}</td>
                      <td className="mono" style={{ textAlign: 'right', color: markColor(h.mark_to_cost) }}>
                        {h.mark_to_cost != null ? h.mark_to_cost.toFixed(3) : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {d.current_holders.length > 30 && (
                <div className="mono dim" style={{ fontSize: 10, marginTop: 4 }}>
                  + {d.current_holders.length - 30} more tranches
                </div>
              )}
            </Panel>
          )}

          {d.filings.length > 0 && (
            <Panel title="EDGAR Filings" subtitle={`${d.filings.length} MATCHING`}>
              <table className="data-table">
                <thead>
                  <tr><th>Filed</th><th>Form</th><th>Company</th><th>Description</th></tr>
                </thead>
                <tbody>
                  {d.filings.slice(0, 20).map((f) => (
                    <tr key={f.accession_no}>
                      <td className="mono dim">{(f.filed_at || '').slice(0, 10)}</td>
                      <td className="mono">{f.form_type}</td>
                      <td style={{ maxWidth: 240, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {f.url ? (
                          <a href={f.url} target="_blank" rel="noopener noreferrer">{f.company_name}</a>
                        ) : f.company_name}
                      </td>
                      <td className="dim" style={{ maxWidth: 380, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {f.description || '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Panel>
          )}

          {d.articles.length > 0 && (
            <Panel title="News" subtitle={`${d.articles.length} ARTICLES · LAST 2 YEARS · FTS`}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {d.articles.map((a) => (
                  <div key={a.id}>
                    <a href={a.url} target="_blank" rel="noopener noreferrer" style={{ fontSize: 13 }}>
                      {a.title}
                    </a>
                    <div className="mono dim" style={{ fontSize: 10, marginTop: 1 }}>
                      {a.feed_name} · {(a.published_at || a.fetched_at || '').slice(0, 10)}
                      {a.relevance_score != null ? ` · score ${a.relevance_score}` : ''}
                    </div>
                  </div>
                ))}
              </div>
            </Panel>
          )}
        </>
      )}
    </div>
  );
}
