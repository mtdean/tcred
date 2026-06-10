// Fed H.8 Bank Credit Monitor — weekly bank-credit table + quarterly credit
// impulse bar chart. Read-only over the cache populated by the FRED scheduler;
// no refresh button on this panel.

import { useQuery } from '@tanstack/react-query';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { getCreditImpulse, getH8Metrics } from '../../lib/api';
import { qk } from '../../lib/queryKeys';
import type { CreditImpulsePoint, H8SeriesMetrics } from '../../lib/types';
import { COLORS } from '../../lib/colors';
import TooltipShell from '../charts/TooltipShell';
import { fmtDate, num, pct, signClass } from '../../lib/utils';
import Panel from '../shared/Panel';
import LoadingCursor from '../shared/LoadingCursor';
import EmptyState from '../shared/EmptyState';

// H.8 series are in $billions on FRED. Format adaptively so trillion-scale
// totals stay readable next to consumer/credit-card sub-tranches.
function fmtDollars(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '—';
  const abs = Math.abs(value);
  if (abs >= 1000) return `$${(value / 1000).toFixed(2)}tn`;
  return `$${num(value, 1)}bn`;
}

function ImpulseTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: { payload: CreditImpulsePoint }[];
}) {
  if (!active || !payload?.length) return null;
  const row = payload[0].payload;
  return (
    <TooltipShell title={fmtDate(row.date)}>
      <div
        style={{
          color:
            row.credit_impulse != null && row.credit_impulse < 0
              ? COLORS.negative
              : COLORS.positive,
        }}
      >
        impulse: {row.credit_impulse != null ? `${row.credit_impulse.toFixed(2)}%` : '—'}
      </div>
      <div style={{ color: COLORS.textSecondary }}>
        ΔQ loans: {fmtDollars(row.qoq_change)}
      </div>
      <div style={{ color: COLORS.textSecondary }}>
        loans: {fmtDollars(row.total_loans)}
      </div>
    </TooltipShell>
  );
}

export default function H8CreditPanel() {
  const metricsQ = useQuery({
    queryKey: qk.h8Metrics,
    queryFn: () => getH8Metrics().then((r) => r.data),
    staleTime: 60 * 60_000,
  });

  const impulseQ = useQuery({
    queryKey: qk.h8CreditImpulse,
    queryFn: () => getCreditImpulse().then((r) => r.data),
    staleTime: 60 * 60_000,
  });

  const metrics: H8SeriesMetrics[] = metricsQ.data ?? [];
  const impulse: CreditImpulsePoint[] = impulseQ.data ?? [];

  // Header subtitle is the as-of week from the first (TOTLL) row when present.
  const asOf = metrics[0]?.latest_date ?? null;

  return (
    <>
      <Panel
        title="Bank Credit Supply (H.8)"
        subtitle={
          asOf
            ? `WEEKLY · AS OF ${fmtDate(asOf).toUpperCase()}`
            : 'WEEKLY ASSETS & LIABILITIES OF COMMERCIAL BANKS'
        }
      >
        {metricsQ.isLoading ? (
          <LoadingCursor />
        ) : metricsQ.isError ? (
          <EmptyState message="FAILED TO LOAD H.8 METRICS" />
        ) : metrics.length === 0 ? (
          <EmptyState message="NO H.8 DATA YET — FRED FETCH PENDING" />
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Series</th>
                <th style={{ textAlign: 'right' }}>Latest</th>
                <th style={{ textAlign: 'right' }}>WoW Δ</th>
                <th style={{ textAlign: 'right' }}>WoW %</th>
                <th style={{ textAlign: 'right' }}>YoY %</th>
                <th style={{ textAlign: 'right' }}>4-wk MA</th>
              </tr>
            </thead>
            <tbody>
              {metrics.map((m) => (
                <tr key={m.series_id}>
                  <td title={m.series_id}>{m.label}</td>
                  <td className="num mono">{fmtDollars(m.latest_value)}</td>
                  <td className={`num mono ${signClass(m.wow_change)}`}>
                    {fmtDollars(m.wow_change)}
                  </td>
                  <td className={`num mono ${signClass(m.wow_pct)}`}>
                    {pct(m.wow_pct, 2)}
                  </td>
                  <td className={`num mono ${signClass(m.yoy_pct)}`}>
                    {pct(m.yoy_pct, 1)}
                  </td>
                  <td className="num mono">{fmtDollars(m.ma4w)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      <Panel
        title="Credit Impulse"
        subtitle="QoQ Δ IN TOTAL BANK LOANS / GDP · ANNUALIZED %"
      >
        {impulseQ.isLoading ? (
          <LoadingCursor />
        ) : impulseQ.isError ? (
          <EmptyState message="FAILED TO LOAD CREDIT IMPULSE" />
        ) : impulse.length === 0 ? (
          <EmptyState message="NEED TOTLL + GDP IN METRICS" />
        ) : (
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={impulse} margin={{ top: 6, right: 12, bottom: 0, left: 0 }}>
              <CartesianGrid stroke={COLORS.border} vertical={false} />
              <XAxis
                dataKey="date"
                tick={{ fill: COLORS.axis, fontSize: 10 }}
                stroke={COLORS.axis}
                minTickGap={32}
                tickFormatter={(v) => {
                  // 'YYYY-MM-DD' → 'YY Qn' for compactness.
                  const [y, m] = String(v).split('-');
                  if (!y || !m) return String(v);
                  const q = Math.floor((parseInt(m, 10) - 1) / 3) + 1;
                  return `${y.slice(2)} Q${q}`;
                }}
              />
              <YAxis
                tick={{ fill: COLORS.axis, fontSize: 10 }}
                tickFormatter={(v) => `${v}`}
                width={42}
                stroke={COLORS.axis}
              />
              <Tooltip content={<ImpulseTooltip />} cursor={{ fill: COLORS.borderBright, fillOpacity: 0.2 }} />
              <ReferenceLine y={0} stroke={COLORS.axis} strokeOpacity={0.5} />
              <Bar dataKey="credit_impulse" isAnimationActive={false}>
                {impulse.map((p) => (
                  <Cell
                    key={p.date}
                    fill={
                      (p.credit_impulse ?? 0) >= 0 ? COLORS.positive : COLORS.negative
                    }
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </Panel>
    </>
  );
}
