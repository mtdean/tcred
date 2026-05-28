// 424B5 new-issue spread tracker. Weekly median spread by asset class +
// rating bucket, plus a trailing-90d deal-volume summary. Data path is
// /abs/spread-series + /abs/deal-summary + /abs/new-issues, all populated by
// data/abs_parser.py (richer schema sibling of AbsPricingPanel's FWP feed).

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { ExternalLink, RefreshCw } from 'lucide-react';

import {
  getAbsDealSummary,
  getAbsNewIssues,
  getAbsSpreadSeries,
  getStatus,
  triggerAbsNewIssuesRefresh,
} from '../../lib/api';
import { qk } from '../../lib/queryKeys';
import type {
  AbsNewIssue,
  AbsRatingBucket,
  AbsSpreadSeriesPoint,
} from '../../lib/types';
import { COLORS } from '../../lib/colors';
import { currency, fmtDate, fmtRelative, num } from '../../lib/utils';
import Panel from '../shared/Panel';
import RangeToggle from '../shared/RangeToggle';
import LoadingCursor from '../shared/LoadingCursor';
import EmptyState from '../shared/EmptyState';

// Asset class taxonomy keys mirror data/abs_parser.py:ASSET_CLASS_TAXONOMY.
const ASSET_CLASSES: { value: string; label: string }[] = [
  { value: 'prime_auto_loan',     label: 'PRIME AUTO LOAN' },
  { value: 'subprime_auto_loan',  label: 'SUBPRIME AUTO LOAN' },
  { value: 'auto_lease',          label: 'AUTO LEASE' },
  { value: 'credit_card',         label: 'CREDIT CARD' },
  { value: 'equipment',           label: 'EQUIPMENT' },
  { value: 'consumer_loan',       label: 'CONSUMER LOAN' },
  { value: 'student_loan',        label: 'STUDENT LOAN' },
  { value: 'solar',               label: 'SOLAR' },
  { value: 'esoteric_wireless',   label: 'WIRELESS' },
  { value: 'esoteric_aircraft',   label: 'AIRCRAFT' },
  { value: 'esoteric_whole_biz',  label: 'WHOLE BUSINESS' },
  { value: 'esoteric_data_center', label: 'DATA CENTER' },
  { value: 'rmbs_non_agency',     label: 'RMBS (NON-AGENCY)' },
  { value: 'cmbs',                label: 'CMBS' },
  { value: 'clo',                 label: 'CLO' },
  { value: 'esoteric_other',      label: 'OTHER ESOTERIC' },
];

const RATING_BUCKETS: readonly AbsRatingBucket[] = ['AAA', 'AA', 'A', 'BBB', 'BB_and_below'] as const;
const RATING_LABEL: Record<AbsRatingBucket, string> = {
  AAA: 'AAA',
  AA: 'AA',
  A: 'A',
  BBB: 'BBB',
  BB_and_below: 'BB↓',
};

const RANGES = [
  { label: '6M', days: 180 },
  { label: '1Y', days: 365 },
  { label: '2Y', days: 730 },
  { label: '3Y', days: 1095 },
] as const;
type RangeLabel = (typeof RANGES)[number]['label'];

// Asset class → display label (for the summary table).
const ASSET_LABEL: Record<string, string> = Object.fromEntries(
  ASSET_CLASSES.map((c) => [c.value, c.label]),
);

// Confidence chip styling.
function confidenceStyle(c: 'high' | 'medium' | 'low'): React.CSSProperties {
  if (c === 'high') {
    return {
      color: 'var(--positive)',
      borderColor: 'var(--positive)',
      background: 'transparent',
    };
  }
  if (c === 'medium') {
    return {
      color: 'var(--neutral)',
      borderColor: 'var(--neutral)',
      borderStyle: 'dashed',
      background: 'transparent',
    };
  }
  return {
    color: 'var(--text-dim)',
    borderColor: 'var(--text-dim)',
    background: 'transparent',
  };
}

// Reshape the series API rows into the chart's data shape. Spreads are stored
// in bps for spread metrics; coupon/yield metrics arrive as percent.
function toChartData(series: AbsSpreadSeriesPoint[]) {
  return series.map((p) => ({
    week: p.week,
    week_start: p.week_start,
    median: p.avg_spread,
    min: p.min_spread,
    max: p.max_spread,
    band: p.max_spread != null && p.min_spread != null
      ? [p.min_spread, p.max_spread]
      : undefined,
    n: p.n_tranches,
  }));
}

function ChartTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: { payload: ReturnType<typeof toChartData>[number] }[];
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  const row = payload[0].payload;
  return (
    <div
      style={{
        background: COLORS.bgPanel,
        border: `1px solid ${COLORS.borderBright}`,
        padding: '4px 8px',
        fontSize: 11,
        fontVariantNumeric: 'tabular-nums',
      }}
    >
      <div style={{ color: COLORS.textSecondary }}>
        wk of {row.week_start ? fmtDate(row.week_start) : label}
      </div>
      <div style={{ color: COLORS.chartPrimary }}>
        median: {row.median != null ? `${row.median.toFixed(0)} bps` : '—'}
      </div>
      {row.min != null && row.max != null && (
        <div style={{ color: COLORS.textSecondary }}>
          range: {row.min.toFixed(0)}–{row.max.toFixed(0)}
        </div>
      )}
      <div style={{ color: COLORS.textSecondary }}>n = {row.n}</div>
    </div>
  );
}

// Render a colored rating chip if any agency has a non-null value.
function RatingChips({ row }: { row: AbsNewIssue }) {
  const items = [
    ['S&P', row.rating_sp],
    ['Moody’s', row.rating_moodys],
    ['KBRA', row.rating_kbra],
    ['Fitch', row.rating_fitch],
  ] as const;
  const present = items.filter(([, v]) => v);
  if (present.length === 0) return <span className="dim">—</span>;
  return (
    <span style={{ display: 'inline-flex', gap: 4, flexWrap: 'wrap' }}>
      {present.map(([k, v]) => (
        <span
          key={k}
          className="mono"
          title={k}
          style={{
            fontSize: 10,
            padding: '1px 4px',
            border: '1px solid var(--border-bright)',
            borderRadius: 2,
            color: 'var(--text-secondary)',
          }}
        >
          {v}
        </span>
      ))}
    </span>
  );
}

export default function SpreadTrackerPanel() {
  const [assetClass, setAssetClass] = useState<string>('prime_auto_loan');
  const [bucket, setBucket] = useState<AbsRatingBucket>('AAA');
  const [range, setRange] = useState<RangeLabel>('1Y');
  const queryClient = useQueryClient();

  const daysBack = RANGES.find((r) => r.label === range)?.days ?? 365;

  // Status drives the "last refreshed" stamp; shares cache with the TopBar
  // query so this doesn't add a request.
  const statusQ = useQuery({
    queryKey: qk.status,
    queryFn: () => getStatus().then((r) => r.data),
    refetchInterval: 30_000,
  });
  const lastRefresh = statusQ.data?.last_abs_424b5_refresh ?? null;

  // Manual refresh: discover + parse the trailing 14d of 424B5s, then nudge
  // the three queries that drive this panel. Mirrors the news REFRESH wiring
  // in TopBar.tsx; status invalidation also updates the timestamp shown here.
  const refresh = useMutation({
    mutationFn: () => triggerAbsNewIssuesRefresh(14).then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['abs', 'new-issues'] });
      queryClient.invalidateQueries({ queryKey: ['abs', 'spread-series'] });
      queryClient.invalidateQueries({ queryKey: ['abs', 'deal-summary'] });
      queryClient.invalidateQueries({ queryKey: qk.status });
    },
  });

  const seriesQ = useQuery({
    queryKey: qk.absSpreadSeries({ asset_class: assetClass, rating_bucket: bucket, days_back: daysBack }),
    queryFn: () => getAbsSpreadSeries({
      asset_class: assetClass,
      rating_bucket: bucket,
      days_back: daysBack,
    }).then((r) => r.data),
    staleTime: 30 * 60_000,
  });

  const summaryQ = useQuery({
    queryKey: qk.absDealSummary(90),
    queryFn: () => getAbsDealSummary(90).then((r) => r.data),
    staleTime: 30 * 60_000,
  });

  const recentQ = useQuery({
    queryKey: qk.absNewIssues({ asset_class: assetClass, limit: 20, days_back: daysBack }),
    queryFn: () =>
      getAbsNewIssues({
        asset_class: assetClass,
        limit: 20,
        days_back: daysBack,
        min_confidence: 'low',  // recent deals table tolerates low; chart filters them out
      }).then((r) => r.data),
    staleTime: 30 * 60_000,
  });

  const data = toChartData(seriesQ.data?.series ?? []);
  const summary = summaryQ.data ?? [];
  const recent = recentQ.data?.items ?? [];

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
          <option key={c.value} value={c.value} style={{ background: 'var(--bg-panel)' }}>
            {c.label}
          </option>
        ))}
      </select>
      <RangeToggle
        options={RATING_BUCKETS.map((b) => RATING_LABEL[b]) as readonly string[]}
        value={RATING_LABEL[bucket]}
        onChange={(lbl) => {
          const found = RATING_BUCKETS.find((b) => RATING_LABEL[b] === lbl);
          if (found) setBucket(found);
        }}
      />
      <RangeToggle
        options={RANGES.map((r) => r.label) as readonly RangeLabel[]}
        value={range}
        onChange={setRange}
      />
      <span
        className="mono"
        style={{
          fontSize: 10,
          color: lastRefresh ? 'var(--text-secondary)' : 'var(--warning)',
          whiteSpace: 'nowrap',
          letterSpacing: 0.5,
        }}
        title={lastRefresh ?? 'never refreshed'}
      >
        {lastRefresh ? fmtRelative(lastRefresh).toUpperCase() : 'NEVER'}
      </span>
      <button
        className="btn"
        onClick={() => refresh.mutate()}
        disabled={refresh.isPending}
        title="Discover and parse the last 14 days of 424B5 ABS filings (Claude fallback on low confidence)"
        style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 10 }}
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
      title="ABS New-Issue Spread Tracker"
      subtitle="424B5 PARSED · WEEKLY MEDIAN bps OVER UST · CONF ≥ MEDIUM"
      actions={actions}
    >
      {/* Chart */}
      {seriesQ.isLoading ? (
        <LoadingCursor />
      ) : seriesQ.isError ? (
        <EmptyState message="FAILED TO LOAD SPREAD SERIES" />
      ) : data.length === 0 ? (
        <EmptyState message="NO 424B5 DATA YET — RUN POST /api/abs/new-issues/refresh" />
      ) : (
        <div style={{ marginBottom: 16 }}>
          <ResponsiveContainer width="100%" height={240}>
            <ComposedChart data={data} margin={{ top: 6, right: 12, bottom: 0, left: 0 }}>
              <CartesianGrid stroke={COLORS.border} vertical={false} />
              <XAxis
                dataKey="week"
                tick={{ fill: COLORS.axis, fontSize: 10 }}
                stroke={COLORS.axis}
                minTickGap={48}
                tickFormatter={(v) => {
                  // 'YYYY-WNN' → 'WNN' for compactness; tooltip carries the full date.
                  const [, w] = String(v).split('-W');
                  return w ? `W${w}` : String(v);
                }}
              />
              <YAxis
                tick={{ fill: COLORS.axis, fontSize: 10 }}
                tickFormatter={(v) => `${Math.round(v)}`}
                width={42}
                stroke={COLORS.axis}
                domain={['auto', 'auto']}
              />
              <Tooltip content={<ChartTooltip />} cursor={{ stroke: COLORS.borderBright }} />
              {/* Min/Max range shaded behind the median. */}
              <Area
                dataKey="band"
                stroke="none"
                fill={COLORS.chartPrimary}
                fillOpacity={0.15}
                isAnimationActive={false}
                connectNulls
              />
              <Line
                type="monotone"
                dataKey="median"
                name="weekly median"
                stroke={COLORS.chartPrimary}
                strokeWidth={1.5}
                dot={{ r: 2.5, fill: COLORS.chartPrimary, stroke: 'none' }}
                isAnimationActive={false}
                connectNulls
              />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* 90d deal-volume summary */}
      <div style={{ marginBottom: 16 }}>
        <div
          className="mono"
          style={{ fontSize: 10, color: 'var(--text-secondary)', letterSpacing: 0.5, marginBottom: 6 }}
        >
          DEAL VOLUME SUMMARY — TRAILING 90D
        </div>
        {summaryQ.isLoading ? (
          <LoadingCursor />
        ) : summaryQ.isError ? (
          <EmptyState message="FAILED TO LOAD DEAL SUMMARY" />
        ) : summary.length === 0 ? (
          <div className="dim" style={{ fontSize: 11 }}>No deals in window.</div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Asset Class</th>
                <th style={{ textAlign: 'right' }}>Deals</th>
                <th style={{ textAlign: 'right' }}>Volume</th>
                <th style={{ textAlign: 'right' }}>Avg Spread</th>
              </tr>
            </thead>
            <tbody>
              {summary.map((r) => (
                <tr key={r.asset_class}>
                  <td>{ASSET_LABEL[r.asset_class] ?? r.asset_class.toUpperCase()}</td>
                  <td style={{ textAlign: 'right' }}>{r.deal_count}</td>
                  <td style={{ textAlign: 'right' }}>
                    {r.total_volume != null ? currency(r.total_volume, 0) : '—'}
                  </td>
                  <td style={{ textAlign: 'right' }}>
                    {r.avg_spread_bps != null ? `${num(r.avg_spread_bps, 0)} bps` : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Recent priced tranches */}
      <div>
        <div
          className="mono"
          style={{ fontSize: 10, color: 'var(--text-secondary)', letterSpacing: 0.5, marginBottom: 6 }}
        >
          RECENT PRICED TRANCHES — {ASSET_LABEL[assetClass] ?? assetClass.toUpperCase()}
        </div>
        {recentQ.isLoading ? (
          <LoadingCursor />
        ) : recentQ.isError ? (
          <EmptyState message="FAILED TO LOAD RECENT TRANCHES" />
        ) : recent.length === 0 ? (
          <div className="dim" style={{ fontSize: 11 }}>No recent tranches for this class.</div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Filed</th>
                <th>Issuer</th>
                <th>Class</th>
                <th style={{ textAlign: 'right' }}>Size</th>
                <th style={{ textAlign: 'right' }}>Cpn / Spr</th>
                <th style={{ textAlign: 'right' }}>WAL</th>
                <th>Ratings</th>
                <th>Conf</th>
                <th style={{ textAlign: 'center' }}>Link</th>
              </tr>
            </thead>
            <tbody>
              {recent.map((r) => (
                <tr key={r.id}>
                  <td className="dim" style={{ whiteSpace: 'nowrap' }}>{fmtDate(r.filing_date)}</td>
                  <td
                    title={r.issuer_name ?? ''}
                    style={{
                      maxWidth: 240,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {r.issuer_name ?? '—'}
                  </td>
                  <td className="mono">{r.class_name}</td>
                  <td style={{ textAlign: 'right' }} className="mono">
                    {r.principal_amount != null ? currency(r.principal_amount, 0) : '—'}
                  </td>
                  <td style={{ textAlign: 'right' }} className="mono">
                    {r.coupon_type === 'floating'
                      ? `${r.floating_index ?? 'FLT'} +${r.floating_spread_bps ?? '—'}`
                      : r.coupon_rate != null
                        ? `${r.coupon_rate.toFixed(2)}%${
                            r.spread_to_benchmark != null ? ` (+${Math.round(r.spread_to_benchmark)})` : ''
                          }`
                        : '—'}
                  </td>
                  <td style={{ textAlign: 'right' }} className="mono">
                    {r.wal_years != null ? `${r.wal_years.toFixed(1)}y` : '—'}
                  </td>
                  <td><RatingChips row={r} /></td>
                  <td>
                    <span
                      className="mono"
                      title={`parse confidence: ${r.parse_confidence}`}
                      style={{
                        fontSize: 9,
                        padding: '1px 4px',
                        border: '1px solid',
                        borderRadius: 2,
                        letterSpacing: 0.5,
                        ...confidenceStyle(r.parse_confidence),
                      }}
                    >
                      {r.parse_confidence.toUpperCase()}
                    </span>
                  </td>
                  <td style={{ textAlign: 'center' }}>
                    {r.edgar_url && (
                      <a
                        href={r.edgar_url}
                        target="_blank"
                        rel="noreferrer"
                        title="Open 424B5 on SEC.gov"
                      >
                        <ExternalLink size={12} />
                      </a>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </Panel>
  );
}
