// Master-trust monthly performance (10-D distribution reports): latest
// delinquency / charge-off / payment-rate per card trust, plus a per-trust
// time series as filings accumulate. Data path is /trust-performance*,
// populated token-free by data/trust_performance.py.

import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { ExternalLink, RefreshCw } from 'lucide-react';

import {
  getTrustPerformance,
  getTrustPerformanceLatest,
  triggerTrustPerformanceRefresh,
} from '../../lib/api';
import { qk } from '../../lib/queryKeys';
import { staticDisabledProps } from '../../lib/staticMode';
import type { TrustPerformanceLatest, TrustPerformanceRow } from '../../lib/types';
import { COLORS } from '../../lib/colors';
import TooltipShell from '../charts/TooltipShell';
import { fmtDate } from '../../lib/utils';
import Panel from '../shared/Panel';
import LoadingCursor from '../shared/LoadingCursor';
import EmptyState from '../shared/EmptyState';

const METRICS: { value: string; label: string }[] = [
  { value: 'net_charge_off_rate', label: 'NET CHARGE-OFF %' },
  { value: 'delinq_30plus_rate', label: '30+ DELINQ %' },
  { value: 'delinq_60plus_rate', label: '60+ DELINQ %' },
  { value: 'delinq_90plus_rate', label: '90+ DELINQ %' },
  { value: 'payment_rate', label: 'PAYMENT RATE %' },
  { value: 'portfolio_yield', label: 'PORTFOLIO YIELD %' },
  { value: 'excess_spread_rate', label: 'EXCESS SPREAD %' },
];

const TRUST_COLORS = [
  COLORS.chartPrimary,
  COLORS.chartSecondary,
  COLORS.chartTertiary,
  COLORS.chart6mo,
  COLORS.negative,
  COLORS.neutral,
  COLORS.chart12mo,
];

// "CHASE ISSUANCE TRUST" → "CHASE", "WORLD FINANCIAL NETWORK …" → "WFN", etc.
function shortName(trust: string): string {
  const t = trust.toUpperCase();
  if (t.startsWith('WORLD FINANCIAL')) return 'WFN / COMENITY';
  if (t.startsWith('WF CARD')) return 'WELLS FARGO';
  return t
    .replace(/\b(CREDIT CARD|CARD)?\s*(ISSUANCE|MASTER NOTE|MASTER)?\s*TRUST\b.*/i, '')
    .trim() || trust;
}

function fmtPct(v: number | undefined): string {
  return v == null ? '—' : `${v.toFixed(2)}%`;
}

function ChartTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: { name?: string; value?: number; color?: string }[];
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <TooltipShell title={fmtDate(label ?? '')}>
      {payload.map((p) => (
        <div key={p.name} style={{ color: p.color }}>
          {p.name}: {p.value != null ? `${Number(p.value).toFixed(2)}%` : '—'}
        </div>
      ))}
    </TooltipShell>
  );
}

function SeriesChart({ rows }: { rows: TrustPerformanceRow[] }) {
  const trusts = useMemo(
    () => [...new Set(rows.map((r) => r.trust_name))].sort(),
    [rows],
  );
  const data = useMemo(() => {
    const byPeriod = new Map<string, Record<string, unknown>>();
    for (const r of rows) {
      const row = byPeriod.get(r.period_end) ?? { period_end: r.period_end };
      row[r.trust_name] = r.value;
      byPeriod.set(r.period_end, row);
    }
    return [...byPeriod.values()].sort((a, b) =>
      String(a.period_end).localeCompare(String(b.period_end)),
    );
  }, [rows]);

  if (data.length === 0) return <EmptyState message="NO TRUST DATA" />;

  return (
    <ResponsiveContainer width="100%" height={220}>
      <LineChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: -14 }}>
        <CartesianGrid stroke={COLORS.border} strokeDasharray="2 4" />
        <XAxis
          dataKey="period_end"
          tick={{ fontSize: 10, fill: COLORS.textSecondary }}
          tickFormatter={(d: string) => fmtDate(d)}
        />
        <YAxis
          tick={{ fontSize: 10, fill: COLORS.textSecondary }}
          tickFormatter={(v: number) => `${v}%`}
          width={56}
        />
        <Tooltip content={<ChartTooltip />} />
        {trusts.map((t, i) => (
          <Line
            key={t}
            dataKey={t}
            name={shortName(t)}
            stroke={TRUST_COLORS[i % TRUST_COLORS.length]}
            strokeWidth={1.5}
            dot={{ r: 2.5 }}
            connectNulls
            isAnimationActive={false}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

export default function TrustPerformancePanel() {
  const [metric, setMetric] = useState('net_charge_off_rate');
  const queryClient = useQueryClient();

  const latestQ = useQuery({
    queryKey: qk.trustPerformanceLatest,
    queryFn: () => getTrustPerformanceLatest().then((r) => r.data),
    staleTime: 30 * 60_000,
  });
  const seriesQ = useQuery({
    queryKey: qk.trustPerformance(metric),
    queryFn: () => getTrustPerformance(metric).then((r) => r.data),
    staleTime: 30 * 60_000,
  });

  const refresh = useMutation({
    mutationFn: () => triggerTrustPerformanceRefresh(35).then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['trust-performance'] });
    },
  });

  const latest: TrustPerformanceLatest[] = latestQ.data ?? [];

  const actions = (
    <div style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
      <select
        value={metric}
        onChange={(e) => setMetric(e.target.value)}
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
        {METRICS.map((m) => (
          <option key={m.value} value={m.value} style={{ background: 'var(--bg-panel)' }}>
            {m.label}
          </option>
        ))}
      </select>
      <button
        className="btn"
        onClick={() => refresh.mutate()}
        disabled={refresh.isPending}
        title="Re-scan EDGAR for new 10-D distribution reports"
        style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 10 }}
        {...staticDisabledProps()}
      >
        <RefreshCw
          size={11}
          style={refresh.isPending ? { animation: 'spin 1s linear infinite' } : undefined}
        />
        {refresh.isPending ? 'PARSING' : 'REFRESH'}
      </button>
    </div>
  );

  return (
    <Panel
      title="Master-Trust Performance (10-D)"
      subtitle="MONTHLY CARD-TRUST DISTRIBUTION REPORTS — LEADS QUARTERLY FRED BY ~2Q"
      actions={actions}
    >
      {latestQ.isLoading ? (
        <LoadingCursor />
      ) : latestQ.isError || latest.length === 0 ? (
        <EmptyState message="NO 10-D DATA — HIT REFRESH" />
      ) : (
        <div className="stack" style={{ gap: 12 }}>
          <table className="data-table">
            <thead>
              <tr>
                <th>TRUST</th>
                <th>PERIOD</th>
                <th style={{ textAlign: 'right' }}>30+ DLQ</th>
                <th style={{ textAlign: 'right' }}>60+ DLQ</th>
                <th style={{ textAlign: 'right' }}>NCO</th>
                <th style={{ textAlign: 'right' }}>PAY RATE</th>
                <th style={{ textAlign: 'right' }}>YIELD</th>
                <th style={{ textAlign: 'right' }}>XS SPREAD</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {latest.map((t) => (
                <tr key={t.trust_name}>
                  <td className="mono">{shortName(t.trust_name)}</td>
                  <td className="mono muted">{fmtDate(t.period_end)}</td>
                  <td className="mono" style={{ textAlign: 'right' }}>
                    {fmtPct(t.metrics.delinq_30plus_rate)}
                  </td>
                  <td className="mono" style={{ textAlign: 'right' }}>
                    {fmtPct(t.metrics.delinq_60plus_rate)}
                  </td>
                  <td className="mono" style={{ textAlign: 'right' }}>
                    {fmtPct(t.metrics.net_charge_off_rate)}
                  </td>
                  <td className="mono" style={{ textAlign: 'right' }}>
                    {fmtPct(t.metrics.payment_rate)}
                  </td>
                  <td className="mono" style={{ textAlign: 'right' }}>
                    {fmtPct(t.metrics.portfolio_yield)}
                  </td>
                  <td className="mono" style={{ textAlign: 'right' }}>
                    {fmtPct(t.metrics.excess_spread_rate)}
                  </td>
                  <td>
                    {t.url && (
                      <a href={t.url} target="_blank" rel="noreferrer" title="Open filing">
                        <ExternalLink size={12} />
                      </a>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {seriesQ.isLoading ? (
            <LoadingCursor />
          ) : (
            <SeriesChart rows={seriesQ.data ?? []} />
          )}
        </div>
      )}
    </Panel>
  );
}
