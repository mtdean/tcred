// BDC Portfolio Monitor — Phase 7 Module 1.
//
// Three stacked panels driven by /api/bdc/*:
//   1. Non-accrual rate trend (line chart, dual Y-axis for mark-to-cost)
//   2. Per-BDC summary table (latest period, sorted by NAV)
//   3. Current non-accruals (latest period, top 100 by cost)
//
// Refresh button hits POST /api/bdc/refresh — downloads ~50MB SEC bulk ZIP,
// parses SOI.tsv, re-computes summaries. Token-free.

import { useMemo } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { RefreshCw } from 'lucide-react';

import {
  getBdcAggregateTrend,
  getBdcLatestPerBdc,
  getBdcNonaccrualTrend,
  getBdcNonaccruals,
  getBdcWatchList,
  triggerBdcRefresh,
} from '../../lib/api';
import { qk } from '../../lib/queryKeys';
import { staticDisabledProps } from '../../lib/staticMode';
import type {
  BdcNonaccrualHolding,
  BdcNonaccrualTrendPoint,
  BdcSummaryRow,
  BdcWatchEntry,
} from '../../lib/types';
import { COLORS } from '../../lib/colors';
import TooltipShell from '../charts/TooltipShell';
import { currency, num } from '../../lib/utils';
import Panel from '../shared/Panel';
import LoadingCursor from '../shared/LoadingCursor';
import EmptyState from '../shared/EmptyState';

// SEC reports period as YYYYMMDD; show MMM YYYY (or fall back to the raw string).
function fmtPeriod(period: string | null | undefined): string {
  if (!period) return '—';
  const s = String(period);
  if (/^\d{8}$/.test(s)) {
    const y = s.slice(0, 4);
    const m = Number(s.slice(4, 6));
    const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    if (m >= 1 && m <= 12) return `${months[m - 1]} ${y}`;
  }
  return s;
}

// Dollars-to-millions (table cells store raw dollars from SEC XBRL).
function mm(value: number | null | undefined, digits = 0): string {
  if (value == null || Number.isNaN(value)) return '—';
  return currency(value / 1_000_000, digits);
}

function pctOf(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) return '—';
  return `${(value * 100).toFixed(digits)}%`;
}

// Color thresholds — risk levels for credit health, mirrors how the spec
// reads them: mark <95 cents = stressed, non-accrual >2% = elevated.
function markColor(mark: number | null | undefined): string {
  if (mark == null) return 'var(--text-secondary)';
  if (mark < 0.95) return 'var(--negative)';
  return 'var(--text-primary)';
}

function nonaccrualColor(rate: number | null | undefined): string {
  if (rate == null) return 'var(--text-secondary)';
  if (rate > 0.02) return 'var(--negative)';
  if (rate > 0.01) return 'var(--warning)';
  return 'var(--text-primary)';
}

const WARNING_HEX = '#ffaa00';   // matches --warning in globals.css
const NEUTRAL_HEX = COLORS.chartSecondary;  // #4a90d9

function ChartTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: { payload: BdcNonaccrualTrendPoint }[];
}) {
  if (!active || !payload?.length) return null;
  const row = payload[0].payload;
  return (
    <TooltipShell title={fmtPeriod(row.period)}>
      <div style={{ color: WARNING_HEX }}>
        non-accrual: {row.avg_nonaccrual_rate != null
          ? `${(row.avg_nonaccrual_rate * 100).toFixed(2)}%`
          : '—'}
      </div>
      <div style={{ color: NEUTRAL_HEX }}>
        mark-to-cost: {row.avg_mark_to_cost != null
          ? row.avg_mark_to_cost.toFixed(3)
          : '—'}
      </div>
      <div style={{ color: COLORS.textSecondary }}>n = {row.n_bdcs}</div>
    </TooltipShell>
  );
}

export default function BDCMonitorPanel() {
  const queryClient = useQueryClient();

  const watchQ = useQuery({
    queryKey: qk.bdcWatchList,
    queryFn: () => getBdcWatchList().then((r) => r.data),
    staleTime: 24 * 3_600_000,
  });

  const trendQ = useQuery({
    queryKey: qk.bdcNonaccrualTrend,
    queryFn: () => getBdcNonaccrualTrend().then((r) => r.data),
    staleTime: 60 * 60_000,
  });

  const summaryQ = useQuery({
    queryKey: qk.bdcLatestPerBdc,
    queryFn: () => getBdcLatestPerBdc().then((r) => r.data),
    staleTime: 60 * 60_000,
  });

  const aggregateQ = useQuery({
    queryKey: qk.bdcAggregateTrend,
    queryFn: () => getBdcAggregateTrend().then((r) => r.data),
    staleTime: 60 * 60_000,
  });

  const nonaccrualsQ = useQuery({
    queryKey: qk.bdcNonaccruals(100),
    queryFn: () => getBdcNonaccruals(100).then((r) => r.data),
    staleTime: 60 * 60_000,
  });

  const refresh = useMutation({
    mutationFn: () => triggerBdcRefresh().then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['bdc'] });
      queryClient.invalidateQueries({ queryKey: qk.status });
    },
  });

  // CIK → ticker for watch-list highlight on the summary table.
  const tickerByCik = useMemo(() => {
    const m = new Map<string, string>();
    (watchQ.data ?? []).forEach((w: BdcWatchEntry) => {
      // CIK can arrive zero-padded or unpadded — index both.
      const cik = String(w.cik || '');
      m.set(cik, w.ticker);
      m.set(cik.replace(/^0+/, ''), w.ticker);
    });
    return m;
  }, [watchQ.data]);

  const trend = trendQ.data ?? [];
  const summary = summaryQ.data ?? [];
  const aggregate = aggregateQ.data ?? [];
  const nonaccruals = nonaccrualsQ.data ?? [];

  const latestTrend = trend.length > 0 ? trend[trend.length - 1] : null;

  const noDataAnywhere =
    !trendQ.isLoading && !summaryQ.isLoading && !nonaccrualsQ.isLoading
    && trend.length === 0 && summary.length === 0 && nonaccruals.length === 0;

  const refreshButton = (
    <button
      className="btn"
      onClick={() => refresh.mutate()}
      disabled={refresh.isPending}
      title="Downloads ~50MB SEC bulk dataset (takes 60-90s)"
      style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 10 }}
      {...staticDisabledProps()}
    >
      <RefreshCw
        size={11}
        style={refresh.isPending ? { animation: 'spin 1s linear infinite' } : undefined}
      />
      {refresh.isPending ? 'DOWNLOADING' : 'REFRESH'}
    </button>
  );

  // Single empty state when there's truly nothing to show.
  if (noDataAnywhere) {
    return (
      <Panel
        title="BDC Non-Accrual Rate"
        subtitle="SEC BDC BULK DATASET · TOKEN-FREE"
        actions={refreshButton}
      >
        <EmptyState message="NO BDC DATA YET — PRESS REFRESH TO DOWNLOAD THE LATEST SEC BULK DATASET" />
      </Panel>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* ── Panel 1: Non-Accrual Trend ──────────────────────────────── */}
      <Panel
        title="BDC Non-Accrual Rate"
        subtitle={
          latestTrend
            ? `LATEST ${fmtPeriod(latestTrend.period).toUpperCase()} · AVG ${
                latestTrend.avg_nonaccrual_rate != null
                  ? `${(latestTrend.avg_nonaccrual_rate * 100).toFixed(2)}%`
                  : '—'
              }`
            : 'SEC BDC BULK DATASET · TOKEN-FREE'
        }
        actions={refreshButton}
      >
        {trendQ.isLoading ? (
          <LoadingCursor />
        ) : trendQ.isError ? (
          <EmptyState message="FAILED TO LOAD NON-ACCRUAL TREND" />
        ) : trend.length === 0 ? (
          <EmptyState message="NO TREND DATA YET — PRESS REFRESH" />
        ) : (
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={trend} margin={{ top: 6, right: 12, bottom: 0, left: 0 }}>
              <CartesianGrid stroke={COLORS.border} vertical={false} />
              <XAxis
                dataKey="period"
                tick={{ fill: COLORS.axis, fontSize: 10 }}
                stroke={COLORS.axis}
                minTickGap={36}
                tickFormatter={(v) => fmtPeriod(String(v))}
              />
              <YAxis
                yAxisId="left"
                tick={{ fill: COLORS.axis, fontSize: 10 }}
                tickFormatter={(v) => `${(v * 100).toFixed(1)}%`}
                width={48}
                stroke={WARNING_HEX}
                domain={['auto', 'auto']}
              />
              <YAxis
                yAxisId="right"
                orientation="right"
                tick={{ fill: COLORS.axis, fontSize: 10 }}
                tickFormatter={(v) => v.toFixed(2)}
                width={48}
                stroke={NEUTRAL_HEX}
                domain={['auto', 'auto']}
              />
              <Tooltip content={<ChartTooltip />} cursor={{ stroke: COLORS.borderBright }} />
              <Line
                yAxisId="left"
                type="monotone"
                dataKey="avg_nonaccrual_rate"
                name="non-accrual rate"
                stroke={WARNING_HEX}
                strokeWidth={1.5}
                dot={{ r: 2.5, fill: WARNING_HEX, stroke: 'none' }}
                isAnimationActive={false}
                connectNulls
              />
              <Line
                yAxisId="right"
                type="monotone"
                dataKey="avg_mark_to_cost"
                name="mark-to-cost"
                stroke={NEUTRAL_HEX}
                strokeWidth={1.5}
                strokeDasharray="3 3"
                dot={{ r: 2.5, fill: NEUTRAL_HEX, stroke: 'none' }}
                isAnimationActive={false}
                connectNulls
              />
            </LineChart>
          </ResponsiveContainer>
        )}
        <div
          className="mono"
          style={{
            fontSize: 10,
            color: 'var(--text-secondary)',
            letterSpacing: 0.5,
            marginTop: 6,
            display: 'flex',
            gap: 16,
          }}
        >
          <span><span style={{ color: WARNING_HEX }}>■</span> NON-ACCRUAL RATE (LEFT)</span>
          <span><span style={{ color: NEUTRAL_HEX }}>■</span> MARK-TO-COST · 1.00 = PAR (RIGHT)</span>
        </div>
      </Panel>

      {/* ── Panel 1.5: Industry Aggregates (NAV / WA rate / mix) ───── */}
      <Panel
        title="Industry Aggregates"
        subtitle="NAV-WEIGHTED · PERIODS WITH ≥5 BDCS REPORTING"
      >
        {aggregateQ.isLoading ? (
          <LoadingCursor />
        ) : aggregateQ.isError ? (
          <EmptyState message="FAILED TO LOAD AGGREGATE TREND" />
        ) : aggregate.length === 0 ? (
          <EmptyState message="NO AGGREGATE DATA YET — PRESS REFRESH" />
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            {/* NAV ($B) trend */}
            <div>
              <div
                className="mono"
                style={{
                  fontSize: 10,
                  color: 'var(--text-secondary)',
                  letterSpacing: 0.5,
                  marginBottom: 2,
                }}
              >
                TOTAL INDUSTRY NAV ($B)
              </div>
              <ResponsiveContainer width="100%" height={150}>
                <LineChart
                  data={aggregate}
                  margin={{ top: 4, right: 12, bottom: 0, left: 0 }}
                >
                  <CartesianGrid stroke={COLORS.border} vertical={false} />
                  <XAxis
                    dataKey="period"
                    tick={{ fill: COLORS.axis, fontSize: 10 }}
                    stroke={COLORS.axis}
                    minTickGap={36}
                    tickFormatter={(v) => fmtPeriod(String(v))}
                  />
                  <YAxis
                    tick={{ fill: COLORS.axis, fontSize: 10 }}
                    tickFormatter={(v: number) => `${(v / 1e9).toFixed(0)}`}
                    width={36}
                    stroke={COLORS.axis}
                    domain={['auto', 'auto']}
                  />
                  <Tooltip
                    contentStyle={{
                      background: COLORS.bgPanel,
                      border: `1px solid ${COLORS.borderBright}`,
                      fontSize: 11,
                    }}
                    labelFormatter={(v) => fmtPeriod(String(v))}
                    formatter={(v) => [`$${(Number(v) / 1e9).toFixed(1)}B`, 'NAV']}
                  />
                  <Line
                    type="monotone"
                    dataKey="total_fv"
                    stroke={COLORS.chartPrimary}
                    strokeWidth={1.5}
                    dot={{ r: 2, fill: COLORS.chartPrimary, stroke: 'none' }}
                    isAnimationActive={false}
                    connectNulls
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>

            {/* WA interest rate (%) */}
            <div>
              <div
                className="mono"
                style={{
                  fontSize: 10,
                  color: 'var(--text-secondary)',
                  letterSpacing: 0.5,
                  marginBottom: 2,
                }}
              >
                NAV-WEIGHTED INTEREST RATE (%)
              </div>
              <ResponsiveContainer width="100%" height={150}>
                <LineChart
                  data={aggregate}
                  margin={{ top: 4, right: 12, bottom: 0, left: 0 }}
                >
                  <CartesianGrid stroke={COLORS.border} vertical={false} />
                  <XAxis
                    dataKey="period"
                    tick={{ fill: COLORS.axis, fontSize: 10 }}
                    stroke={COLORS.axis}
                    minTickGap={36}
                    tickFormatter={(v) => fmtPeriod(String(v))}
                  />
                  <YAxis
                    tick={{ fill: COLORS.axis, fontSize: 10 }}
                    tickFormatter={(v: number) => `${(v * 100).toFixed(1)}%`}
                    width={42}
                    stroke={COLORS.axis}
                    domain={['auto', 'auto']}
                  />
                  <Tooltip
                    contentStyle={{
                      background: COLORS.bgPanel,
                      border: `1px solid ${COLORS.borderBright}`,
                      fontSize: 11,
                    }}
                    labelFormatter={(v) => fmtPeriod(String(v))}
                    formatter={(v) => [`${(Number(v) * 100).toFixed(2)}%`, 'WA rate']}
                  />
                  <Line
                    type="monotone"
                    dataKey="wa_interest_rate"
                    stroke={COLORS.chartSecondary}
                    strokeWidth={1.5}
                    dot={{ r: 2, fill: COLORS.chartSecondary, stroke: 'none' }}
                    isAnimationActive={false}
                    connectNulls
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>

            {/* Portfolio mix: 1L / 2L / Equity stacked area */}
            <div>
              <div
                className="mono"
                style={{
                  fontSize: 10,
                  color: 'var(--text-secondary)',
                  letterSpacing: 0.5,
                  marginBottom: 2,
                }}
              >
                PORTFOLIO MIX (1L / 2L / EQUITY · % OF NAV)
              </div>
              <ResponsiveContainer width="100%" height={170}>
                <AreaChart
                  data={aggregate}
                  margin={{ top: 4, right: 12, bottom: 0, left: 0 }}
                >
                  <CartesianGrid stroke={COLORS.border} vertical={false} />
                  <XAxis
                    dataKey="period"
                    tick={{ fill: COLORS.axis, fontSize: 10 }}
                    stroke={COLORS.axis}
                    minTickGap={36}
                    tickFormatter={(v) => fmtPeriod(String(v))}
                  />
                  <YAxis
                    tick={{ fill: COLORS.axis, fontSize: 10 }}
                    tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
                    width={42}
                    stroke={COLORS.axis}
                    domain={[0, 'auto']}
                  />
                  <Tooltip
                    contentStyle={{
                      background: COLORS.bgPanel,
                      border: `1px solid ${COLORS.borderBright}`,
                      fontSize: 11,
                    }}
                    labelFormatter={(v) => fmtPeriod(String(v))}
                    formatter={(v, name) => [
                      `${(Number(v) * 100).toFixed(1)}%`,
                      String(name),
                    ]}
                  />
                  <Area
                    type="monotone"
                    dataKey="pct_first_lien"
                    name="1st lien"
                    stackId="mix"
                    stroke={COLORS.positive}
                    fill={COLORS.positive}
                    fillOpacity={0.6}
                    isAnimationActive={false}
                  />
                  <Area
                    type="monotone"
                    dataKey="pct_second_lien"
                    name="2nd lien"
                    stackId="mix"
                    stroke={WARNING_HEX}
                    fill={WARNING_HEX}
                    fillOpacity={0.6}
                    isAnimationActive={false}
                  />
                  <Area
                    type="monotone"
                    dataKey="pct_equity"
                    name="equity"
                    stackId="mix"
                    stroke={COLORS.negative}
                    fill={COLORS.negative}
                    fillOpacity={0.6}
                    isAnimationActive={false}
                  />
                </AreaChart>
              </ResponsiveContainer>
              <div
                className="mono"
                style={{
                  fontSize: 10,
                  color: 'var(--text-secondary)',
                  letterSpacing: 0.5,
                  marginTop: 2,
                  display: 'flex',
                  gap: 12,
                }}
              >
                <span><span style={{ color: COLORS.positive }}>■</span> 1L</span>
                <span><span style={{ color: WARNING_HEX }}>■</span> 2L</span>
                <span><span style={{ color: COLORS.negative }}>■</span> EQUITY</span>
              </div>
            </div>
          </div>
        )}
      </Panel>

      {/* ── Panel 2: Per-BDC Summary (most recent per BDC) ──────────── */}
      <Panel
        title="Per-BDC Summary"
        subtitle={
          summary.length > 0
            ? `MOST RECENT FILING PER BDC · ${summary.length} BDC${summary.length === 1 ? '' : 'S'}`
            : undefined
        }
      >
        {summaryQ.isLoading ? (
          <LoadingCursor />
        ) : summaryQ.isError ? (
          <EmptyState message="FAILED TO LOAD BDC SUMMARY" />
        ) : summary.length === 0 ? (
          <EmptyState message="NO SUMMARY DATA YET — PRESS REFRESH" />
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>BDC</th>
                <th>As of</th>
                <th style={{ textAlign: 'right' }}>NAV ($mm)</th>
                <th style={{ textAlign: 'right' }}>Mark %</th>
                <th style={{ textAlign: 'right' }}>Non-Accrual %</th>
                <th style={{ textAlign: 'right' }}>1st Lien %</th>
                <th style={{ textAlign: 'right' }}>WA Rate</th>
                <th style={{ textAlign: 'right' }}>n Holdings</th>
              </tr>
            </thead>
            <tbody>
              {summary.map((r: BdcSummaryRow) => {
                const ticker = tickerByCik.get(r.cik) ?? tickerByCik.get(r.cik.replace(/^0+/, ''));
                const tickerChip = ticker ? (
                  <span
                    className="mono"
                    style={{
                      fontSize: 10,
                      padding: '1px 4px',
                      border: '1px solid var(--border-bright)',
                      borderRadius: 2,
                      marginRight: 6,
                      color: 'var(--accent)',
                      letterSpacing: 0.5,
                    }}
                  >
                    {ticker}
                  </span>
                ) : null;
                return (
                  <tr key={r.id}>
                    <td>
                      {tickerChip && r.filing_url ? (
                        <a
                          href={r.filing_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          title={`open EDGAR filing ${r.adsh ?? ''}`}
                          style={{ textDecoration: 'none' }}
                        >
                          {tickerChip}
                        </a>
                      ) : (
                        tickerChip
                      )}
                      <span
                        title={r.bdc_name}
                        style={{
                          display: 'inline-block',
                          maxWidth: 260,
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                          verticalAlign: 'bottom',
                        }}
                      >
                        {r.bdc_name}
                      </span>
                    </td>
                    <td className="mono dim" style={{ fontSize: 11 }}>
                      {fmtPeriod(r.period)}
                    </td>
                    <td className="mono" style={{ textAlign: 'right' }}>
                      {mm(r.total_fair_value, 0)}
                    </td>
                    <td
                      className="mono"
                      style={{ textAlign: 'right', color: markColor(r.mark_to_cost) }}
                    >
                      {r.mark_to_cost != null ? `${(r.mark_to_cost * 100).toFixed(1)}%` : '—'}
                    </td>
                    <td
                      className="mono"
                      style={{ textAlign: 'right', color: nonaccrualColor(r.nonaccrual_rate_fv) }}
                    >
                      {pctOf(r.nonaccrual_rate_fv)}
                    </td>
                    <td className="mono" style={{ textAlign: 'right' }}>
                      {pctOf(r.pct_first_lien, 1)}
                    </td>
                    <td className="mono" style={{ textAlign: 'right' }}>
                      {r.wa_interest_rate != null
                        ? `${(r.wa_interest_rate * 100).toFixed(2)}%`
                        : '—'}
                    </td>
                    <td className="mono" style={{ textAlign: 'right' }}>
                      {r.n_holdings != null ? num(r.n_holdings, 0) : '—'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Panel>

      {/* ── Panel 3: Current Non-Accruals ───────────────────────────── */}
      <Panel
        title="Current Non-Accruals"
        subtitle={
          nonaccruals.length > 0
            ? `${nonaccruals.length} HOLDING${nonaccruals.length === 1 ? '' : 'S'} · LATEST ${
                fmtPeriod(nonaccruals[0].period).toUpperCase()
              }`
            : undefined
        }
      >
        {nonaccrualsQ.isLoading ? (
          <LoadingCursor />
        ) : nonaccrualsQ.isError ? (
          <EmptyState message="FAILED TO LOAD NON-ACCRUALS" />
        ) : nonaccruals.length === 0 ? (
          <EmptyState message="NO NON-ACCRUALS IN LATEST PERIOD" />
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Company</th>
                <th>BDC</th>
                <th>Industry</th>
                <th>Type</th>
                <th style={{ textAlign: 'right' }}>Cost ($mm)</th>
                <th style={{ textAlign: 'right' }}>FV ($mm)</th>
                <th style={{ textAlign: 'right' }}>Mark %</th>
              </tr>
            </thead>
            <tbody>
              {nonaccruals.map((r: BdcNonaccrualHolding, i: number) => {
                const mark =
                  r.fair_value != null && r.cost_basis != null && r.cost_basis !== 0
                    ? r.fair_value / r.cost_basis
                    : null;
                return (
                  <tr key={`${r.bdc_name}-${r.company_name}-${i}`}>
                    <td
                      title={r.company_name ?? ''}
                      style={{
                        maxWidth: 260,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {r.company_name ?? '—'}
                    </td>
                    <td
                      className="dim"
                      title={r.bdc_name}
                      style={{
                        maxWidth: 200,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {r.bdc_name}
                    </td>
                    <td className="dim">{r.industry || '—'}</td>
                    <td className="dim">{r.investment_type || '—'}</td>
                    <td className="mono" style={{ textAlign: 'right' }}>
                      {mm(r.cost_basis, 1)}
                    </td>
                    <td className="mono" style={{ textAlign: 'right' }}>
                      {mm(r.fair_value, 1)}
                    </td>
                    <td
                      className="mono"
                      style={{ textAlign: 'right', color: markColor(mark) }}
                    >
                      {mark != null ? `${(mark * 100).toFixed(1)}%` : '—'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Panel>
    </div>
  );
}
