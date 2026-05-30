// Issuer / deal pivot — type a name, see every angle we have on it:
// recent deals, all EDGAR filings, KBRA presales, scored articles.

import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Search } from 'lucide-react';
import { getIssuerSummary, listIssuers, type IssuerDeal } from '../lib/api';
import type { EdgarFiling } from '../lib/types';
import { qk } from '../lib/queryKeys';
import { fmtDateTime } from '../lib/utils';
import Panel from '../components/shared/Panel';
import LoadingCursor from '../components/shared/LoadingCursor';
import ScoreDots from '../components/shared/ScoreDots';

function fmtMillions(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '—';
  if (n >= 1_000_000_000) return `$${(n / 1_000_000_000).toFixed(2)}B`;
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(0)}M`;
  return `$${n.toLocaleString()}`;
}

function fmtBps(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '—';
  return `${n.toFixed(0)} bps`;
}

export default function DealsPage() {
  const [draft, setDraft] = useState('');
  const [query, setQuery] = useState('');

  // Universe of issuer names with the most recent activity.
  const { data: universe } = useQuery({
    queryKey: qk.issuers,
    queryFn: () => listIssuers(60).then((r) => r.data.items),
  });

  // Run the search whenever `query` is non-empty.
  const summary = useQuery({
    queryKey: qk.issuerSummary(query),
    queryFn: () => getIssuerSummary(query).then((r) => r.data),
    enabled: query.length > 0,
  });

  // Default to the most recently active issuer in the universe.
  useEffect(() => {
    if (!query && universe && universe.length > 0) {
      const root = trustRootName(universe[0].issuer_name);
      setQuery(root);
      setDraft(root);
    }
  }, [universe, query]);

  return (
    <div className="stack">
      <Panel
        title="Issuer / Deal Pivot"
        subtitle="ALL DATA FOR ONE NAME · SUBSTRING · CASE-INSENSITIVE"
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            setQuery(draft.trim());
          }}
          style={{ display: 'flex', gap: 8, marginBottom: 8 }}
        >
          <input
            type="text"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="e.g. Carvana, Santander Drive, Honda…"
            style={{
              flex: 1,
              background: 'var(--bg-panel-alt)',
              color: 'var(--text-primary)',
              border: '1px solid var(--border-bright)',
              padding: '6px 10px',
              fontSize: 12,
              fontFamily: 'inherit',
              borderRadius: 2,
            }}
          />
          <button
            type="submit"
            className="btn"
            disabled={!draft.trim()}
            style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
          >
            <Search size={12} />
            SEARCH
          </button>
        </form>

        {universe && universe.length > 0 && (
          <div style={{ marginBottom: 4 }}>
            <div
              className="muted"
              style={{ fontSize: 10, letterSpacing: 1, marginBottom: 4 }}
            >
              RECENT ISSUERS
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
              {dedupeRoots(universe.map((u) => u.issuer_name))
                .slice(0, 24)
                .map((name) => {
                  const root = trustRootName(name);
                  const active = root === query;
                  return (
                    <button
                      key={name}
                      onClick={() => {
                        setDraft(root);
                        setQuery(root);
                      }}
                      className="cat-chip"
                      style={{
                        cursor: 'pointer',
                        border: `1px solid ${active ? 'var(--text-primary)' : 'var(--border)'}`,
                        color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
                        background: active ? 'var(--bg-panel-alt)' : 'transparent',
                        padding: '1px 6px',
                        fontSize: 10,
                      }}
                    >
                      {root}
                    </button>
                  );
                })}
            </div>
          </div>
        )}
      </Panel>

      {query && summary.isLoading && (
        <Panel title={`Loading "${query}"`}>
          <LoadingCursor />
        </Panel>
      )}

      {summary.data && (
        <>
          <StatsPanel stats={summary.data.stats} query={summary.data.query} />
          <DealsPanel deals={summary.data.deals} />
          <FilingsPanel filings={summary.data.edgar_filings} />
          {summary.data.kbra_presales.length > 0 && (
            <KbraPanel presales={summary.data.kbra_presales} />
          )}
          {summary.data.articles.length > 0 && (
            <ArticlesPanel articles={summary.data.articles} />
          )}
          {summary.data.deals.length === 0 &&
            summary.data.edgar_filings.length === 0 &&
            summary.data.articles.length === 0 && (
              <Panel title="No matches">
                <div className="muted" style={{ fontSize: 12 }}>
                  Nothing in our data matches "{summary.data.query}". Try a shorter
                  substring (e.g. "Carvana" instead of "Carvana Auto Receivables
                  Trust 2026-1").
                </div>
              </Panel>
            )}
        </>
      )}
    </div>
  );
}

// ─── Stats summary ──────────────────────────────────────────────────────────
function StatsPanel({
  stats,
  query,
}: {
  stats: {
    n_deals: number;
    total_volume: number;
    earliest_filing: string | null;
    latest_filing: string | null;
    n_asset_classes: number;
  };
  query: string;
}) {
  const span =
    stats.earliest_filing && stats.latest_filing
      ? `${stats.earliest_filing} → ${stats.latest_filing}`
      : '—';
  return (
    <Panel title={query} subtitle="ABS NEW-ISSUE ACTIVITY">
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(4, 1fr)',
          gap: 10,
          fontSize: 12,
        }}
      >
        <StatTile label="Deals" value={stats.n_deals.toString()} />
        <StatTile label="Total volume" value={fmtMillions(stats.total_volume)} />
        <StatTile label="Asset classes" value={stats.n_asset_classes.toString()} />
        <StatTile label="Span" value={span} mono={false} />
      </div>
    </Panel>
  );
}

function StatTile({
  label,
  value,
  mono = true,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div style={{ padding: '4px 0' }}>
      <div
        className="muted"
        style={{ fontSize: 10, letterSpacing: 1, marginBottom: 2 }}
      >
        {label.toUpperCase()}
      </div>
      <div className={mono ? 'mono' : undefined} style={{ fontSize: 14 }}>
        {value}
      </div>
    </div>
  );
}

// ─── Deals table ────────────────────────────────────────────────────────────
function DealsPanel({ deals }: { deals: IssuerDeal[] }) {
  if (deals.length === 0) {
    return (
      <Panel title="Deals" subtitle="0 MATCHES">
        <div className="muted" style={{ fontSize: 12 }}>
          No 424B5 deals parsed for this query.
        </div>
      </Panel>
    );
  }
  return (
    <Panel title="Deals" subtitle={`${deals.length} ABS NEW-ISSUE DEALS`}>
      <table className="data-table">
        <thead>
          <tr>
            <th>Trust</th>
            <th style={{ width: 130 }}>Asset Class</th>
            <th style={{ width: 100 }}>Filed</th>
            <th style={{ width: 90, textAlign: 'right' }}>Tranches</th>
            <th style={{ width: 110, textAlign: 'right' }}>Total</th>
            <th style={{ width: 130, textAlign: 'right' }}>Senior WAL</th>
            <th style={{ width: 110, textAlign: 'right' }}>Senior Spread</th>
          </tr>
        </thead>
        <tbody>
          {deals.map((d) => (
            <tr key={d.accession_no}>
              <td>
                {d.edgar_url ? (
                  <a href={d.edgar_url} target="_blank" rel="noopener noreferrer">
                    {d.issuer_name ?? d.accession_no}
                  </a>
                ) : (
                  d.issuer_name ?? d.accession_no
                )}
              </td>
              <td className="muted">{d.asset_class ?? '—'}</td>
              <td className="num dim">{d.filing_date ?? '—'}</td>
              <td className="num mono">{d.n_tranches}</td>
              <td className="num mono">{fmtMillions(d.total_deal_size)}</td>
              <td className="num mono">
                {d.senior_wal_years != null
                  ? `${d.senior_class_name ?? ''} · ${d.senior_wal_years.toFixed(2)}y`
                  : '—'}
              </td>
              <td className="num mono">{fmtBps(d.senior_spread_bps)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

// ─── EDGAR filings ──────────────────────────────────────────────────────────
function FilingsPanel({ filings }: { filings: EdgarFiling[] }) {
  if (filings.length === 0) return null;
  return (
    <Panel title="EDGAR Filings" subtitle={`${filings.length} MATCHES`}>
      <table className="data-table">
        <thead>
          <tr>
            <th style={{ width: 90 }}>Form</th>
            <th>Issuer</th>
            <th style={{ width: 130 }}>Asset Class</th>
            <th style={{ width: 100 }}>Filed</th>
          </tr>
        </thead>
        <tbody>
          {filings.map((f) => (
            <tr key={f.accession_no}>
              <td className="mono">{f.form_type ?? '—'}</td>
              <td>
                {f.url ? (
                  <a href={f.url} target="_blank" rel="noopener noreferrer">
                    {f.company_name ?? f.accession_no}
                  </a>
                ) : (
                  f.company_name ?? f.accession_no
                )}
              </td>
              <td className="muted">{f.asset_class ?? '—'}</td>
              <td className="num dim">{f.filed_at ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

// ─── KBRA presales ──────────────────────────────────────────────────────────
function KbraPanel({ presales }: { presales: Record<string, unknown>[] }) {
  return (
    <Panel title="KBRA Presales" subtitle={`${presales.length} MATCHES`}>
      <table className="data-table">
        <thead>
          <tr>
            <th>Deal</th>
            <th style={{ width: 130 }}>Asset Class</th>
            <th style={{ width: 100 }}>Closing</th>
            <th style={{ width: 90, textAlign: 'right' }}>Base CDR</th>
            <th style={{ width: 90, textAlign: 'right' }}>Base CPR</th>
            <th style={{ width: 90, textAlign: 'right' }}>CE AAA</th>
          </tr>
        </thead>
        <tbody>
          {presales.map((p, i) => (
            <tr key={`${(p.deal_name as string) ?? 'kbra'}-${i}`}>
              <td>{(p.deal_name as string) ?? '—'}</td>
              <td className="muted">{(p.asset_class as string) ?? '—'}</td>
              <td className="num dim">{(p.closing_date as string) ?? '—'}</td>
              <td className="num mono">{fmtPct(p.base_cdr)}</td>
              <td className="num mono">{fmtPct(p.base_cpr)}</td>
              <td className="num mono">{fmtPct(p.ce_aaa)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

function fmtPct(n: unknown): string {
  if (typeof n !== 'number' || !Number.isFinite(n)) return '—';
  return `${n.toFixed(2)}%`;
}

// ─── Articles ───────────────────────────────────────────────────────────────
function ArticlesPanel({
  articles,
}: {
  articles: {
    id: string;
    feed_name: string;
    title: string;
    url: string;
    published_at: string | null;
    fetched_at: string;
    relevance_score: number | null;
  }[];
}) {
  return (
    <Panel title="News" subtitle={`${articles.length} MATCHES · SCORE ≥ 3 · 180D`}>
      <table className="data-table">
        <thead>
          <tr>
            <th style={{ width: 60 }}>Score</th>
            <th>Title</th>
            <th style={{ width: 130 }}>Source</th>
            <th style={{ width: 140 }}>Published</th>
          </tr>
        </thead>
        <tbody>
          {articles.map((a) => (
            <tr key={a.id}>
              <td><ScoreDots score={a.relevance_score} /></td>
              <td>
                <a href={a.url} target="_blank" rel="noopener noreferrer">
                  {a.title}
                </a>
              </td>
              <td className="muted">{a.feed_name}</td>
              <td className="num dim">
                {fmtDateTime(a.published_at ?? a.fetched_at)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

// ─── Issuer name helpers ────────────────────────────────────────────────────
// Trust names typically encode "<sponsor family> <vehicle> <series>".
// We want the chip set to dedupe by sponsor family, not by series — so we
// strip the trailing "Trust YYYY-X" / "Receivables Trust" boilerplate before
// using the head of the name as a search query.
const TRUST_BOILERPLATE = /\s+(Auto\s+(?:Receivables|Owner|Lease)\s+(?:Trust|Issuance)|Receivables\s+(?:Trust|Issuance)|Owner\s+Trust|Issuance\s+Trust|Master\s+Trust|Funding\s+(?:LLC|Trust)|Trust|Series).*$/i;

function trustRootName(name: string): string {
  const cleaned = name.replace(TRUST_BOILERPLATE, '').trim();
  // If stripping ate the whole string, fall back to the original.
  return cleaned.length >= 3 ? cleaned : name;
}

function dedupeRoots(names: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const n of names) {
    const r = trustRootName(n);
    const k = r.toLowerCase();
    if (!seen.has(k)) {
      seen.add(k);
      out.push(n);
    }
  }
  return out;
}
