// SIFMA monthly ABS issuance, broken out by asset class.
// Source flow: user drops the SIFMA xlsx into backend/cache/sifma_drops/, the
// scheduled _job_sifma parses it into the metrics table, and this panel
// reads it back. REFRESH triggers an ad-hoc rescan.

import { useMemo } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { RefreshCw } from 'lucide-react';
import {
  Area, AreaChart, CartesianGrid, Legend, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from 'recharts';
import { getAbsIssuance, triggerAbsIssuanceRefresh } from '../../lib/api';
import { qk } from '../../lib/queryKeys';
import { staticDisabledProps } from '../../lib/staticMode';
import { fmtRelative } from '../../lib/utils';
import Panel from '../shared/Panel';
import LoadingCursor from '../shared/LoadingCursor';

// Visual order + colors per asset class. Keeping them stacked makes the
// total-issuance trend readable while preserving the per-class composition.
const STACK_ORDER: { id: string; label: string; color: string }[] = [
  { id: 'SIFMA_AUTO',    label: 'Auto',         color: '#5fa8d3' },
  { id: 'SIFMA_CC',      label: 'Credit Cards', color: '#f4a261' },
  { id: 'SIFMA_EQUIP',   label: 'Equipment',    color: '#9b8cc0' },
  { id: 'SIFMA_STUDENT', label: 'Student',      color: '#62b6a3' },
  { id: 'SIFMA_HE',      label: 'Home Equity',  color: '#e76f51' },
  { id: 'SIFMA_MH',      label: 'Mfg Housing',  color: '#a3a380' },
  { id: 'SIFMA_OTHER',   label: 'Other',        color: '#8d8d8d' },
];

interface MergedRow {
  date: string;
  [seriesId: string]: number | string;
}

function fmtMoney(n: number | undefined | null): string {
  if (n == null || !Number.isFinite(n)) return '—';
  if (Math.abs(n) >= 100) return `$${n.toFixed(0)}B`;
  return `$${n.toFixed(1)}B`;
}

export default function SifmaPanel() {
  const queryClient = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: qk.absIssuance,
    queryFn: () => getAbsIssuance().then((r) => r.data),
    staleTime: 30 * 60_000,
  });

  const refresh = useMutation({
    mutationFn: triggerAbsIssuanceRefresh,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: qk.absIssuance }),
  });

  // Pivot per-series points into one row per date so AreaChart can stack.
  const { rows, present, trailing12mo, latestMonth } = useMemo(() => {
    if (!data?.sifma) return { rows: [], present: [], trailing12mo: 0, latestMonth: null as string | null };

    const seriesMap = data.sifma;
    const byDate = new Map<string, MergedRow>();
    for (const cfg of STACK_ORDER) {
      const s = seriesMap[cfg.id];
      if (!s) continue;
      for (const p of s.points) {
        const row = byDate.get(p.date) ?? { date: p.date };
        row[cfg.id] = p.value;
        byDate.set(p.date, row);
      }
    }
    const rs = [...byDate.values()].sort((a, b) =>
      (a.date as string).localeCompare(b.date as string),
    );
    const presentSeries = STACK_ORDER.filter((c) => !!seriesMap[c.id]);

    // Trailing 12 months of total (use SIFMA_TOTAL if present, else sum the
    // stacked classes — which mirrors how SIFMA itself reports the headline).
    const last12 = rs.slice(-12);
    const totalSeries = seriesMap['SIFMA_TOTAL']?.points ?? [];
    const totalLast12 = totalSeries.slice(-12);
    const trailing =
      totalLast12.length === 12
        ? totalLast12.reduce((acc, p) => acc + p.value, 0)
        : last12.reduce(
            (acc, r) =>
              acc + presentSeries.reduce((c, cfg) => c + ((r[cfg.id] as number) ?? 0), 0),
            0,
          );

    return {
      rows: rs,
      present: presentSeries,
      trailing12mo: trailing,
      latestMonth: rs.length ? (rs[rs.length - 1].date as string) : null,
    };
  }, [data]);

  const hasData = rows.length > 0;
  const subtitle =
    hasData
      ? `${rows.length} MONTHS · LATEST ${latestMonth} · TRAILING 12MO ${fmtMoney(trailing12mo)}`
      : data?.last_ingest
        ? `LAST INGEST ${fmtRelative(data.last_ingest)} — NO RECORDS PARSED`
        : 'NO DATA YET · DROP THE LATEST SIFMA XLSX INTO CACHE/SIFMA_DROPS/';

  return (
    <Panel
      title="SIFMA ABS Issuance"
      subtitle={subtitle}
      actions={
        <button
          className="btn"
          onClick={() => refresh.mutate()}
          disabled={refresh.isPending}
          title="Rescan the drop folder + refresh FRED supplement"
          style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
          {...staticDisabledProps()}
        >
          <RefreshCw
            size={12}
            style={refresh.isPending ? { animation: 'spin 1s linear infinite' } : undefined}
          />
          {refresh.isPending ? 'SCANNING' : 'REFRESH'}
        </button>
      }
    >
      {isLoading && !data ? (
        <LoadingCursor />
      ) : !hasData ? (
        <div
          style={{
            border: '1px dashed var(--border-bright)',
            padding: '14px 16px',
            color: 'var(--text-dim)',
            fontSize: 12,
            lineHeight: 1.6,
          }}
        >
          <div
            className="muted"
            style={{ fontSize: 11, letterSpacing: 1, marginBottom: 6, color: 'var(--text-secondary)' }}
          >
            DROP-FOLDER INGEST
          </div>
          <div className="prose">
            SIFMA's xlsx download is gated by a form. Manual workflow:
          </div>
          <ol style={{ margin: '6px 0 0 18px', padding: 0 }}>
            <li>
              Download "US ABS Issuance" from{' '}
              <a
                href="https://www.sifma.org/research/statistics/us-asset-backed-securities-statistics/"
                target="_blank"
                rel="noopener noreferrer"
              >
                sifma.org
              </a>
              .
            </li>
            <li>
              Drop the file into{' '}
              <span className="mono" style={{ color: 'var(--text-primary)' }}>
                {data?.drop_dir ?? 'backend/cache/sifma_drops/'}
              </span>
              .
            </li>
            <li>Press REFRESH (or wait — scheduler scans every 6h).</li>
          </ol>
        </div>
      ) : (
        <div style={{ height: 320 }}>
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={rows} margin={{ top: 6, right: 10, left: 0, bottom: 4 }}>
              <CartesianGrid stroke="var(--border)" vertical={false} />
              <XAxis
                dataKey="date"
                tick={{ fontSize: 10, fill: 'var(--text-secondary)' }}
                tickFormatter={(d: string) => d.slice(0, 7)}
                stroke="var(--border-bright)"
              />
              <YAxis
                tick={{ fontSize: 10, fill: 'var(--text-secondary)' }}
                tickFormatter={(v: number) => `$${v}B`}
                stroke="var(--border-bright)"
                width={48}
              />
              <Tooltip
                contentStyle={{
                  background: 'var(--bg-panel-alt)',
                  border: '1px solid var(--border-bright)',
                  fontSize: 11,
                  borderRadius: 2,
                }}
                labelStyle={{ color: 'var(--text-primary)' }}
                itemStyle={{ color: 'var(--text-secondary)' }}
                formatter={(v) => fmtMoney(typeof v === 'number' ? v : null)}
              />
              <Legend wrapperStyle={{ fontSize: 10 }} iconSize={8} />
              {present
                .filter((p) => p.id !== 'SIFMA_TOTAL')
                .map((cfg) => (
                  <Area
                    key={cfg.id}
                    type="monotone"
                    dataKey={cfg.id}
                    stackId="issuance"
                    name={cfg.label}
                    stroke={cfg.color}
                    fill={cfg.color}
                    fillOpacity={0.55}
                  />
                ))}
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </Panel>
  );
}
