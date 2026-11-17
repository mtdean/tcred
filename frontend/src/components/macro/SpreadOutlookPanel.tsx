// Spread Outlook — macrobot `credit_spread` distribution forecast.
//
// Spread levels aren't forecastable beyond no-change, so the path is a median
// (roughly flat) inside a calibrated fan learned from 40 years of Baa−10y
// history, with explicit odds of a large widening / tightening over 12 months.

import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { COLORS } from '../../lib/colors';
import type { MacroViews, SpreadOutlookIndex } from '../../lib/types';
import Panel from '../shared/Panel';
import TooltipShell from '../charts/TooltipShell';
import DocLink from './DocLink';

const bpOf = (pct: number | null | undefined) => (pct == null ? '—' : `${Math.round(pct * 100)}bp`);
const prob = (p: number | null | undefined) => (p == null ? '—' : `${Math.round(p * 100)}%`);

function Stat({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div style={{ flex: '1 1 110px' }}>
      <div className="muted" style={{ fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase' }}>{label}</div>
      <div style={{ fontSize: 17, fontWeight: 700, color: color ?? COLORS.textPrimary }}>{value}</div>
      {sub && <div className="muted" style={{ fontSize: 10 }}>{sub}</div>}
    </div>
  );
}

function IndexFan({ name, idx, color }: { name: string; idx: SpreadOutlookIndex; color: string }) {
  const rows = idx.path.map((p) => ({
    h: p.h === 0 ? 'now' : `+${p.h}m`,
    outer: p.p05 != null && p.p95 != null ? [p.p05 * 100, p.p95 * 100] : [p.median * 100, p.median * 100],
    inner: p.p16 != null && p.p84 != null ? [p.p16 * 100, p.p84 * 100] : [p.median * 100, p.median * 100],
    median: p.median * 100,
  }));
  const end = idx.path[idx.path.length - 1];
  const rich = (idx.gap ?? 0) < 0;
  return (
    <div style={{ border: `1px solid ${COLORS.border}`, padding: '10px 12px', background: COLORS.bgPanel }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, marginBottom: 6 }}>
        <Stat label={`${name} OAS now`} value={bpOf(idx.now)}
          sub={`12m median ${bpOf(end.median)}`} />
        <Stat label="12m range (90%)" value={`${bpOf(end.p05)} – ${bpOf(end.p95)}`}
          sub={`68%: ${bpOf(end.p16)} – ${bpOf(end.p84)}`} />
        <Stat label={`P(≥ ${idx.widen_bp}bp wider)`} value={prob(idx.prob_widen)}
          sub="within 12m" color={COLORS.negative} />
        <Stat label={`P(≥ ${idx.tighten_bp}bp tighter)`} value={prob(idx.prob_tighten)}
          sub="within 12m" color={COLORS.positive} />
        <Stat label="vs fair value" value={idx.gap == null ? '—' : `${idx.gap >= 0 ? '+' : ''}${Math.round(idx.gap * 100)}bp`}
          sub={rich ? 'rich (tight vs macro)' : 'cheap (wide vs macro)'}
          color={rich ? COLORS.accent : COLORS.positive} />
      </div>
      <ResponsiveContainer width="100%" height={210}>
        <ComposedChart data={rows} margin={{ top: 6, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={COLORS.border} vertical={false} />
          <XAxis dataKey="h" tick={{ fill: COLORS.axis, fontSize: 10 }} stroke={COLORS.axis} />
          <YAxis tick={{ fill: COLORS.axis, fontSize: 10 }} stroke={COLORS.axis} width={44}
            tickFormatter={(v) => `${Math.round(v)}`} domain={['auto', 'auto']} />
          <Tooltip content={({ active, payload, label }) => {
            if (!active || !payload?.length) return null;
            const r = payload[0].payload as (typeof rows)[number];
            return (
              <TooltipShell title={label}>
                <div style={{ color }}>median {Math.round(r.median)}bp</div>
                <div style={{ color: COLORS.textSecondary }}>68%: {Math.round(r.inner[0])} – {Math.round(r.inner[1])}bp</div>
                <div style={{ color: COLORS.textSecondary }}>90%: {Math.round(r.outer[0])} – {Math.round(r.outer[1])}bp</div>
              </TooltipShell>
            );
          }} />
          <Area dataKey="outer" stroke="none" fill={color} fillOpacity={0.10} isAnimationActive={false} />
          <Area dataKey="inner" stroke="none" fill={color} fillOpacity={0.20} isAnimationActive={false} />
          {idx.fair_value != null && (
            <ReferenceLine y={idx.fair_value * 100} stroke={COLORS.textSecondary} strokeDasharray="5 4"
              label={{ value: 'macro fair value', fill: COLORS.textSecondary, fontSize: 9, position: 'insideTopRight' }} />
          )}
          <Line dataKey="median" stroke={color} strokeWidth={2.5} dot={{ r: 2, fill: color }} isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

export default function SpreadOutlookPanel({ views }: { views: MacroViews }) {
  const so = views.spread_outlook;
  if (!so || Object.keys(so.indices).length === 0) return null;
  const meth = views.methodology?.spread_outlook;
  return (
    <Panel
      title="Spread Outlook (12m)"
      subtitle={
        <span>
          median path · dark band 68% · light band 90% · bp ·
          {so.valuation_z != null && <> valuation {so.valuation_z >= 0 ? '+' : ''}{so.valuation_z.toFixed(1)}σ vs macro fair value ·</>}
          {' '}levels aren&apos;t forecastable beyond no-change — the fan and odds are the forecast
          {meth && <> <DocLink m={meth} /></>}
        </span>
      }
    >
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(420px, 1fr))', gap: 12 }}>
        {so.indices.hy && <IndexFan name="HY" idx={so.indices.hy} color={COLORS.negative} />}
        {so.indices.ig && <IndexFan name="IG" idx={so.indices.ig} color={COLORS.chartSecondary} />}
      </div>
    </Panel>
  );
}
