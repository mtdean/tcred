// Consumer Health Scorecard — one narrative-ready table: each indicator's
// latest value, short-term trend, and 5-year STRESS percentile (100 = max
// stress), plus a composite headline. Backed by GET /api/scorecard/consumer.

import { useQuery } from '@tanstack/react-query';
import { getConsumerScorecard } from '../../lib/api';
import type { ScorecardIndicator } from '../../lib/types';
import Panel from '../shared/Panel';
import LoadingCursor from '../shared/LoadingCursor';
import EmptyState from '../shared/EmptyState';

function fmtVal(i: ScorecardIndicator): string {
  const v = i.value;
  if (v == null) return '—';
  const digits = Math.abs(v) >= 100 ? 0 : Math.abs(v) >= 10 ? 1 : 2;
  return `${v.toFixed(digits)}${i.unit}`;
}

function TrendArrow({ trend }: { trend: string | null }) {
  if (trend === 'worsening')
    return <span style={{ color: 'var(--negative)' }}>▲ WORSE</span>;
  if (trend === 'improving')
    return <span style={{ color: 'var(--positive)' }}>▼ BETTER</span>;
  if (trend === 'flat') return <span style={{ color: 'var(--text-secondary)' }}>— FLAT</span>;
  return <span style={{ color: 'var(--text-dim)' }}>—</span>;
}

function StressBar({ pctl }: { pctl: number | null }) {
  if (pctl == null)
    return <span className="mono dim" style={{ fontSize: 10 }}>n/a</span>;
  const color =
    pctl >= 80 ? 'var(--negative)' : pctl >= 60 ? 'var(--warning)' : 'var(--positive)';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <div
        style={{
          flex: 1,
          maxWidth: 90,
          height: 5,
          background: 'var(--border)',
          borderRadius: 2,
          overflow: 'hidden',
        }}
      >
        <div style={{ width: `${pctl}%`, height: '100%', background: color }} />
      </div>
      <span className="mono" style={{ fontSize: 10, color, minWidth: 24, textAlign: 'right' }}>
        {pctl.toFixed(0)}
      </span>
    </div>
  );
}

export default function ConsumerScorecardPanel() {
  const q = useQuery({
    queryKey: ['scorecard', 'consumer'],
    queryFn: () => getConsumerScorecard().then((r) => r.data),
    staleTime: 60 * 60_000,
  });

  const d = q.data;
  const composite = d?.composite_stress;
  const compositeColor =
    composite == null
      ? 'var(--text-secondary)'
      : composite >= 70
        ? 'var(--negative)'
        : composite >= 50
          ? 'var(--warning)'
          : 'var(--positive)';

  return (
    <Panel
      title="Consumer Health Scorecard"
      subtitle={
        d
          ? `${d.n_worsening}/${d.n_indicators} WORSENING · STRESS PCTL = WHERE TODAY SITS IN 5Y (100 = MAX STRESS)`
          : 'LATEST · TREND · 5Y STRESS PERCENTILE'
      }
      actions={
        composite != null ? (
          <span className="mono" style={{ fontSize: 12 }}>
            COMPOSITE{' '}
            <span style={{ color: compositeColor, fontWeight: 700 }}>
              {composite.toFixed(0)}
            </span>
          </span>
        ) : undefined
      }
    >
      {q.isLoading ? (
        <LoadingCursor />
      ) : q.isError || !d || d.indicators.length === 0 ? (
        <EmptyState message="NO SCORECARD DATA — REFRESH MACRO" />
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>INDICATOR</th>
              <th style={{ textAlign: 'right' }}>LATEST</th>
              <th>TREND (~3M)</th>
              <th>5Y STRESS PCTL</th>
            </tr>
          </thead>
          <tbody>
            {d.indicators.map((i) => (
              <tr key={i.id}>
                <td>
                  {i.label}
                  {i.source === '10-D' && (
                    <span
                      className="mono"
                      style={{ fontSize: 9, color: 'var(--accent)', marginLeft: 5 }}
                    >
                      10-D
                    </span>
                  )}
                </td>
                <td className="mono" style={{ textAlign: 'right' }}>{fmtVal(i)}</td>
                <td className="mono" style={{ fontSize: 10 }}>
                  <TrendArrow trend={i.trend} />
                </td>
                <td><StressBar pctl={i.stress_percentile} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}
