// Policy Path — macrobot `policy_path`.
//
// Fed funds path implied by Treasury forwards, corrected for forwards'
// historical over-prediction, inside a band built from the method's own past
// errors. The Taylor rule is a gauge of policy stance, not the forecast.

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
import { COLORS } from '../../lib/colors';
import type { MacroViews } from '../../lib/types';
import Panel from '../shared/Panel';
import TooltipShell from '../charts/TooltipShell';
import DocLink from './DocLink';

const pct = (x: number | null | undefined) => (x == null ? '—' : `${x.toFixed(2)}%`);
const signed = (x: number | null | undefined, unit: string) =>
  x == null ? '—' : `${x >= 0 ? '+' : ''}${Math.round(x * 100)}${unit}`;

function Tile({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div style={{ border: `1px solid ${COLORS.border}`, padding: '8px 12px', flex: '1 1 150px',
      background: COLORS.bgPanel }}>
      <div className="muted" style={{ fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase' }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700, marginTop: 2, color: color ?? COLORS.textPrimary }}>{value}</div>
      {sub && <div className="muted" style={{ fontSize: 10, marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

export default function PolicyPathPanel({ views }: { views: MacroViews }) {
  const pp = views.policy_path;
  if (!pp || pp.path.length < 2) return null;
  const now = pp.path[0];
  const end = pp.path[pp.path.length - 1];
  const easy = (pp.rule_gap ?? 0) < 0;
  const meth = views.methodology?.policy_path;
  const rows = pp.path.map((p) => ({
    h: p.h === 0 ? 'now' : `+${p.h}m`,
    band: [p.lo, p.hi],
    path: p.point,
    raw: p.raw,
    rule: p.rule,
  }));
  return (
    <Panel
      title="Policy Path (Fed Funds, 12m)"
      subtitle={<span>bias-corrected market path · band = 68% of past errors · dashed = raw forwards · dotted = Taylor rule{meth && <> <DocLink m={meth} /></>}</span>}
    >
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginBottom: 10 }}>
        <Tile label="Fed funds now" value={pct(now.point)} />
        <Tile label="Path in 12m" value={pct(end.point)}
          sub={`${signed(end.point - now.point, 'bp')} · 68% ${pct(end.lo)} – ${pct(end.hi)}`}
          color={COLORS.accent} />
        <Tile label="Raw forwards in 12m" value={pct(end.raw)}
          sub="before removing forward bias" />
        <Tile label="Taylor-rule prescription" value={pct(pp.rule_prescription)}
          sub={`core PCE ${pp.core_pce_yoy?.toFixed(1) ?? '—'}% y/y · unemp gap ${pp.ugap?.toFixed(1) ?? '—'}pp`} />
        <Tile label="Policy vs rule" value={signed(pp.rule_gap, 'bp')}
          sub={easy ? 'easy relative to the rule' : 'tight relative to the rule'}
          color={easy ? COLORS.positive : COLORS.negative} />
      </div>
      <ResponsiveContainer width="100%" height={210}>
        <ComposedChart data={rows} margin={{ top: 6, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={COLORS.border} vertical={false} />
          <XAxis dataKey="h" tick={{ fill: COLORS.axis, fontSize: 10 }} stroke={COLORS.axis} />
          <YAxis tick={{ fill: COLORS.axis, fontSize: 10 }} stroke={COLORS.axis} width={40}
            tickFormatter={(v) => v.toFixed(1)} domain={['auto', 'auto']} />
          <Tooltip content={({ active, payload, label }) => {
            if (!active || !payload?.length) return null;
            const r = payload[0].payload as (typeof rows)[number];
            return (
              <TooltipShell title={label}>
                <div style={{ color: COLORS.accent }}>path {pct(r.path)}</div>
                <div style={{ color: COLORS.textSecondary }}>68%: {pct(r.band[0])} – {pct(r.band[1])}</div>
                <div style={{ color: COLORS.textSecondary }}>raw forwards {pct(r.raw)}</div>
                {r.rule != null && <div style={{ color: COLORS.chart6mo }}>rule {pct(r.rule)}</div>}
              </TooltipShell>
            );
          }} />
          <Area dataKey="band" stroke="none" fill={COLORS.accent} fillOpacity={0.14} isAnimationActive={false} />
          <Line dataKey="raw" stroke={COLORS.textSecondary} strokeDasharray="5 4" dot={false} strokeWidth={1.2} isAnimationActive={false} />
          <Line dataKey="rule" stroke={COLORS.chart6mo} strokeDasharray="2 3" dot={{ r: 2 }} strokeWidth={1.2} connectNulls isAnimationActive={false} />
          <Line dataKey="path" stroke={COLORS.accent} strokeWidth={2.5} dot={{ r: 2, fill: COLORS.accent }} isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </Panel>
  );
}
