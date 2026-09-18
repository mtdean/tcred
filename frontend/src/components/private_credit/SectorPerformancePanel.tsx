// Sector Performance — private credit marks by industry, from the SEC BDC
// bulk dataset's Industry Sector Axis breakdowns (bdc_industry table).
//
// Two views driven by GET /api/bdc/sector-trend:
//   1. Mark-to-cost by sector over time (line chart, top-7 sectors charted,
//      toggleable legend chips). 1.00 = portfolio held at cost.
//   2. Latest-quarter table across ALL sectors: FV, share, mark, QoQ change.

import { Fragment, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { getBdcSectorDetail, getBdcSectorTrend } from '../../lib/api';
import { qk } from '../../lib/queryKeys';
import type { BdcSectorTrendPoint } from '../../lib/types';
import { COLORS } from '../../lib/colors';
import TooltipShell from '../charts/TooltipShell';
import { currency } from '../../lib/utils';
import Panel from '../shared/Panel';
import LoadingCursor from '../shared/LoadingCursor';
import EmptyState from '../shared/EmptyState';

// Fixed sector → hue mapping (color follows the entity, never its rank).
// Palette validated for CVD + contrast on the #111 surface; sectors outside
// this map appear in the table but are not charted.
const SECTOR_COLORS: Record<string, string> = {
  'Software & Tech': '#cc7a00',
  'Healthcare': '#4a90d9',
  'Business Services': '#008f5d',
  'Consumer & Retail': '#8e5cc7',
  'Financials & Insurance': '#d6367e',
  'Industrials': '#a08508',
  'Media & Telecom': '#c04a52',
};
const CHARTED_SECTORS = Object.keys(SECTOR_COLORS);

// Periods arrive as ISO dates ("2026-06-30").
function fmtPeriod(period: string | null | undefined, long = false): string {
  if (!period) return '—';
  const m = /^(\d{4})-(\d{2})-\d{2}$/.exec(String(period));
  if (!m) return String(period);
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const mon = months[Number(m[2]) - 1] ?? m[2];
  return long ? `${mon} ${m[1]}` : `${mon} ${m[1].slice(2)}`;
}

function markColor(mark: number | null | undefined): string {
  if (mark == null) return 'var(--text-secondary)';
  if (mark < 0.95) return 'var(--negative)';
  if (mark < 0.98) return 'var(--warning)';
  return 'var(--text-primary)';
}

type PivotRow = { period: string } & Record<string, number | string | null>;

function SectorTooltip({
  active,
  label,
  payload,
}: {
  active?: boolean;
  label?: string;
  payload?: { dataKey?: string | number; value?: number | string }[];
}) {
  if (!active || !payload?.length) return null;
  const rows = payload
    .filter((p) => typeof p.value === 'number')
    .sort((a, b) => Number(b.value) - Number(a.value));
  return (
    <TooltipShell title={fmtPeriod(String(label), true)}>
      {rows.map((p) => (
        <div key={String(p.dataKey)} style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <span style={{ color: SECTOR_COLORS[String(p.dataKey)] ?? COLORS.textSecondary }}>■</span>
          <span style={{ color: COLORS.textSecondary }}>{String(p.dataKey)}</span>
          <span style={{ color: COLORS.textPrimary, marginLeft: 'auto' }}>
            {Number(p.value).toFixed(3)}
          </span>
        </div>
      ))}
    </TooltipShell>
  );
}

export default function SectorPerformancePanel() {
  const trendQ = useQuery({
    queryKey: qk.bdcSectorTrend,
    queryFn: () => getBdcSectorTrend().then((r) => r.data),
    staleTime: 60 * 60_000,
  });

  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const toggle = (sector: string) =>
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(sector)) next.delete(sector);
      else next.add(sector);
      return next;
    });

  // Per-BDC drill-down: one payload for all sectors, expanded rows render
  // from it instantly (also snapshot-friendly for the gh-pages build).
  const [expanded, setExpanded] = useState<string | null>(null);
  const detailQ = useQuery({
    queryKey: qk.bdcSectorDetail,
    queryFn: () => getBdcSectorDetail().then((r) => r.data),
    staleTime: 60 * 60_000,
  });

  const rows = useMemo(() => trendQ.data ?? [], [trendQ.data]);

  const periods = useMemo(
    () => [...new Set(rows.map((r) => r.period))].sort(),
    [rows],
  );
  const latestPeriod = periods[periods.length - 1];
  const prevPeriod = periods[periods.length - 2];

  // Pivot (period, sector) rows → one object per period for the line chart.
  const chartData: PivotRow[] = useMemo(
    () =>
      periods.map((p) => {
        const row: PivotRow = { period: p };
        rows
          .filter((r) => r.period === p && r.mark_to_cost != null)
          .forEach((r) => {
            row[r.sector] = r.mark_to_cost;
          });
        return row;
      }),
    [rows, periods],
  );

  const latest = useMemo(
    () =>
      rows
        .filter((r) => r.period === latestPeriod)
        .sort((a, b) => (b.total_fv ?? 0) - (a.total_fv ?? 0)),
    [rows, latestPeriod],
  );
  const prevBySector = useMemo(() => {
    const m = new Map<string, BdcSectorTrendPoint>();
    rows.filter((r) => r.period === prevPeriod).forEach((r) => m.set(r.sector, r));
    return m;
  }, [rows, prevPeriod]);

  const visibleSectors = CHARTED_SECTORS.filter((s) => !hidden.has(s));

  return (
    <Panel
      title="Sector Performance"
      subtitle={
        latestPeriod
          ? `MARK-TO-COST BY INDUSTRY · ${fmtPeriod(latestPeriod, true).toUpperCase()} · SEC BDC XBRL`
          : 'MARK-TO-COST BY INDUSTRY · SEC BDC XBRL'
      }
    >
      {trendQ.isLoading ? (
        <LoadingCursor />
      ) : trendQ.isError ? (
        <EmptyState message="FAILED TO LOAD SECTOR TREND" />
      ) : rows.length === 0 ? (
        <EmptyState message="NO SECTOR DATA YET — PRESS REFRESH ON THE BDC PANEL BELOW" />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {/* legend chips — click to toggle a sector line */}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {CHARTED_SECTORS.map((s) => {
              const off = hidden.has(s);
              return (
                <button
                  key={s}
                  className="mono"
                  onClick={() => toggle(s)}
                  title={off ? `show ${s}` : `hide ${s}`}
                  style={{
                    fontSize: 10,
                    letterSpacing: 0.5,
                    padding: '2px 7px',
                    borderRadius: 2,
                    cursor: 'pointer',
                    background: 'transparent',
                    border: `1px solid ${off ? 'var(--border)' : SECTOR_COLORS[s]}`,
                    color: off ? 'var(--text-dim)' : 'var(--text-primary)',
                  }}
                >
                  <span style={{ color: off ? 'var(--text-dim)' : SECTOR_COLORS[s] }}>■</span>
                  {' '}{s.toUpperCase()}
                </button>
              );
            })}
          </div>

          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={chartData} margin={{ top: 6, right: 34, bottom: 0, left: 0 }}>
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
                tickFormatter={(v: number) => v.toFixed(2)}
                width={44}
                stroke={COLORS.axis}
                domain={['auto', 'auto']}
              />
              <ReferenceLine
                y={1}
                stroke={COLORS.textSecondary}
                strokeDasharray="4 4"
                label={{
                  value: 'PAR',
                  position: 'right',
                  fill: COLORS.textSecondary,
                  fontSize: 9,
                }}
              />
              <Tooltip content={<SectorTooltip />} cursor={{ stroke: COLORS.borderBright }} />
              {visibleSectors.map((s) => (
                <Line
                  key={s}
                  type="monotone"
                  dataKey={s}
                  stroke={SECTOR_COLORS[s]}
                  strokeWidth={1.5}
                  dot={{ r: 2, fill: SECTOR_COLORS[s], stroke: 'none' }}
                  isAnimationActive={false}
                  connectNulls
                />
              ))}
            </LineChart>
          </ResponsiveContainer>

          <div
            className="mono"
            style={{ fontSize: 10, color: 'var(--text-secondary)', letterSpacing: 0.5 }}
          >
            FAIR VALUE ÷ COST BASIS, AGGREGATED ACROSS BDC PORTFOLIOS · 1.00 = HELD AT COST
          </div>

          {/* latest-quarter table across all sectors */}
          <table className="data-table">
            <thead>
              <tr>
                <th>Sector</th>
                <th style={{ textAlign: 'right' }}>FV ($B)</th>
                <th style={{ textAlign: 'right' }}>Share</th>
                <th style={{ textAlign: 'right' }}>Mark</th>
                <th style={{ textAlign: 'right' }}>Δ QoQ (bps)</th>
                <th style={{ textAlign: 'right' }}>n BDCs</th>
              </tr>
            </thead>
            <tbody>
              {latest.map((r) => {
                const prev = prevBySector.get(r.sector);
                const delta =
                  r.mark_to_cost != null && prev?.mark_to_cost != null
                    ? (r.mark_to_cost - prev.mark_to_cost) * 10_000
                    : null;
                const hue = SECTOR_COLORS[r.sector];
                const isOpen = expanded === r.sector;
                const detail = detailQ.data?.sectors?.[r.sector] ?? [];
                return (
                  <Fragment key={r.sector}>
                  <tr
                    onClick={() => setExpanded(isOpen ? null : r.sector)}
                    style={{ cursor: 'pointer' }}
                    title={isOpen ? 'collapse' : 'show per-BDC detail'}
                  >
                    <td>
                      <span
                        className="mono"
                        style={{ color: 'var(--text-dim)', marginRight: 5, fontSize: 9 }}
                      >
                        {isOpen ? '▼' : '▶'}
                      </span>
                      {hue && (
                        <span style={{ color: hue, marginRight: 6 }}>■</span>
                      )}
                      {r.sector}
                    </td>
                    <td className="mono" style={{ textAlign: 'right' }}>
                      {r.total_fv != null ? currency(r.total_fv / 1e9, 1) : '—'}
                    </td>
                    <td className="mono" style={{ textAlign: 'right' }}>
                      {r.fv_share != null ? `${(r.fv_share * 100).toFixed(1)}%` : '—'}
                    </td>
                    <td
                      className="mono"
                      style={{ textAlign: 'right', color: markColor(r.mark_to_cost) }}
                    >
                      {r.mark_to_cost != null ? r.mark_to_cost.toFixed(3) : '—'}
                    </td>
                    <td
                      className="mono"
                      style={{
                        textAlign: 'right',
                        color:
                          delta == null
                            ? 'var(--text-secondary)'
                            : delta < 0
                              ? 'var(--negative)'
                              : 'var(--positive)',
                      }}
                    >
                      {delta != null ? `${delta > 0 ? '+' : ''}${delta.toFixed(0)}` : '—'}
                    </td>
                    <td className="mono dim" style={{ textAlign: 'right' }}>
                      {r.n_bdcs}
                    </td>
                  </tr>
                  {isOpen && (
                    <tr>
                      <td colSpan={6} style={{ padding: '4px 8px 10px 22px' }}>
                        {detailQ.isLoading ? (
                          <LoadingCursor />
                        ) : detail.length === 0 ? (
                          <span className="mono dim" style={{ fontSize: 10 }}>
                            NO PER-BDC DETAIL FOR THIS SECTOR
                          </span>
                        ) : (
                          <>
                            <div
                              className="mono"
                              style={{
                                fontSize: 10,
                                color: 'var(--text-secondary)',
                                letterSpacing: 0.5,
                                margin: '2px 0 4px',
                              }}
                            >
                              PER-BDC MARKS · {detail.length} BDCS ·{' '}
                              {fmtPeriod(detailQ.data?.prior_period, true).toUpperCase()} →{' '}
                              {fmtPeriod(detailQ.data?.latest_period, true).toUpperCase()}
                            </div>
                            <table className="data-table" style={{ fontSize: 11 }}>
                              <thead>
                                <tr>
                                  <th>BDC</th>
                                  <th style={{ textAlign: 'right' }}>FV ($mm)</th>
                                  <th style={{ textAlign: 'right' }}>Mark</th>
                                  <th style={{ textAlign: 'right' }}>Δ QoQ (bps)</th>
                                </tr>
                              </thead>
                              <tbody>
                                {detail.slice(0, 12).map((d) => (
                                  <tr key={d.cik}>
                                    <td
                                      title={d.bdc_name}
                                      style={{
                                        maxWidth: 320,
                                        overflow: 'hidden',
                                        textOverflow: 'ellipsis',
                                        whiteSpace: 'nowrap',
                                      }}
                                    >
                                      {d.bdc_name}
                                    </td>
                                    <td className="mono" style={{ textAlign: 'right' }}>
                                      {d.fair_value != null
                                        ? currency(d.fair_value / 1e6, 0)
                                        : '—'}
                                    </td>
                                    <td
                                      className="mono"
                                      style={{
                                        textAlign: 'right',
                                        color: markColor(d.mark_to_cost),
                                      }}
                                    >
                                      {d.mark_to_cost != null
                                        ? d.mark_to_cost.toFixed(3)
                                        : '—'}
                                    </td>
                                    <td
                                      className="mono"
                                      style={{
                                        textAlign: 'right',
                                        color:
                                          d.delta_bps == null
                                            ? 'var(--text-secondary)'
                                            : d.delta_bps < 0
                                              ? 'var(--negative)'
                                              : 'var(--positive)',
                                      }}
                                    >
                                      {d.delta_bps != null
                                        ? `${d.delta_bps > 0 ? '+' : ''}${d.delta_bps.toFixed(0)}`
                                        : '—'}
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                            {detail.length > 12 && (
                              <div
                                className="mono dim"
                                style={{ fontSize: 10, marginTop: 3 }}
                              >
                                + {detail.length - 12} smaller BDCs
                              </div>
                            )}
                          </>
                        )}
                      </td>
                    </tr>
                  )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
