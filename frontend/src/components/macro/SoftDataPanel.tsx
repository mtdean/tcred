// Soft Data — Beige Book credit stress behind the default view.
//
// macrobot scores every Beige Book full report (2001+) for credit-deterioration
// language vs easing language and overall tone; the stress z-score is a leading
// driver in default_cycle_soft. Shows the index history, the latest release's
// dominant stress terms, and verbatim quotes so the read is auditable.

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
import { COLORS } from '../../lib/colors';
import type { FilingsDistressPoint, MacroViews } from '../../lib/types';
import Panel from '../shared/Panel';
import TooltipShell from '../charts/TooltipShell';
import DocLink from './DocLink';

const z = (x: number | null | undefined) => (x == null ? '—' : `${x >= 0 ? '+' : ''}${x.toFixed(2)}σ`);

function Tile({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div style={{ border: `1px solid ${COLORS.border}`, padding: '8px 12px', flex: '1 1 160px',
      background: COLORS.bgPanel }}>
      <div className="muted" style={{ fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase' }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700, marginTop: 2, color: color ?? COLORS.textPrimary }}>{value}</div>
      {sub && <div className="muted" style={{ fontSize: 10, marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

export default function SoftDataPanel({ views }: { views: MacroViews }) {
  const sd = views.soft_data;
  if (!sd || sd.history.length === 0) return null;
  const ev = views.regime?.evidence ?? {};
  const stress = ev.bb_credit_stress_z as number | undefined;
  const stressChg = ev.bb_credit_stress_z_chg as number | undefined;
  const tone = ev.bb_tone_z as number | undefined;
  const hard4q = ev.default_rate_4q_fcst_pct as number | undefined;
  const soft4q = ev.default_rate_4q_fcst_soft_pct as number | undefined;
  const stressColor = stress == null ? COLORS.textPrimary
    : stress > 0.5 ? COLORS.negative : stress < -0.5 ? COLORS.positive : COLORS.textPrimary;
  const meth = views.methodology?.soft_data;
  return (
    <Panel
      title="Soft Data — Beige Book Credit Stress"
      subtitle={<span>{sd.source} · latest {sd.latest_release}{meth && <> <DocLink m={meth} /></>}</span>}
    >
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginBottom: 12 }}>
        <Tile label="Credit stress (z)" value={z(stress)} color={stressColor}
          sub={`vs prior release ${z(stressChg)}`} />
        <Tile label="Economic tone (z)" value={z(tone)}
          color={tone == null ? undefined : tone >= 0 ? COLORS.positive : COLORS.negative} />
        <Tile label="Loss rate 4q — hard data" value={hard4q == null ? '—' : `${hard4q.toFixed(2)}%`}
          sub="default_cycle (spreads, slope, unemployment)" />
        <Tile label="Loss rate 4q — + soft data" value={soft4q == null ? '—' : `${soft4q.toFixed(2)}%`}
          sub="+ SLOOS tightening + Beige Book stress" color={COLORS.accent} />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 16 }}>
        <div>
          <div className="muted" style={{ fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase' }}>
            Index history by release (z-score since 2001)
          </div>
          <ResponsiveContainer width="100%" height={210}>
            <LineChart data={sd.history} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
              <CartesianGrid stroke={COLORS.border} strokeDasharray="2 4" />
              <XAxis dataKey="date" tick={{ fill: COLORS.textSecondary, fontSize: 10 }} stroke={COLORS.border}
                tickFormatter={(d: string) => d.slice(0, 7)} minTickGap={40} />
              <YAxis tick={{ fill: COLORS.textSecondary, fontSize: 10 }} stroke={COLORS.border} width={36} />
              <ReferenceLine y={0} stroke={COLORS.borderBright} />
              <Tooltip content={({ active, payload, label }) =>
                active && payload?.length ? (
                  <TooltipShell title={label}>
                    {payload.map((p) => (
                      <div key={String(p.dataKey)} style={{ color: p.color }}>
                        {p.name}: {z(Number(p.value))}
                      </div>
                    ))}
                  </TooltipShell>
                ) : null} />
              <Line dataKey="credit_stress_z" name="Credit stress" stroke={COLORS.negative}
                dot={false} strokeWidth={1.6} isAnimationActive={false} />
              <Line dataKey="tone_z" name="Tone" stroke={COLORS.chartSecondary}
                dot={false} strokeWidth={1.2} strokeDasharray="4 3" isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <div>
          <div className="muted" style={{ fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase' }}>
            What the latest release says
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, margin: '6px 0 8px' }}>
            {Object.entries(sd.top_terms).map(([term, n]) => (
              <span key={term} style={{ fontSize: 10, border: `1px solid ${COLORS.borderBright}`,
                padding: '1px 6px', color: COLORS.textSecondary }}>
                {term} <span style={{ color: COLORS.accent }}>×{n}</span>
              </span>
            ))}
          </div>
          {sd.quotes.map((q, i) => (
            <blockquote key={i} style={{ margin: '0 0 6px', padding: '2px 0 2px 8px', fontSize: 11,
              borderLeft: `2px solid ${COLORS.negative}`, color: COLORS.textPrimary, lineHeight: 1.4 }}>
              “{q}”
            </blockquote>
          ))}
        </div>
      </div>
      {sd.filings && sd.filings.length > 0 && <FilingsSection rows={sd.filings} views={views} />}
    </Panel>
  );
}

// EDGAR filings distress: management's own words (going concern, forbearance,
// restructuring support agreements, Chapter 11 petitions) as shares of filings.
function FilingsSection({ rows, views }: { rows: FilingsDistressPoint[]; views: MacroViews }) {
  const last = rows[rows.length - 1];
  const yearAgo = rows.length > 4 ? rows[rows.length - 5] : undefined;
  const meth = views.methodology?.filings_distress;
  const comp = last.composite_z;
  const chg = comp != null && yearAgo?.composite_z != null ? comp - yearAgo.composite_z : null;
  const color = comp == null ? undefined : comp > 0.5 ? COLORS.negative : comp < -0.5 ? COLORS.positive : undefined;
  const lines: { key: keyof FilingsDistressPoint; name: string; color: string; dash?: string }[] = [
    { key: 'composite_z', name: 'Composite', color: COLORS.accent },
    { key: 'going_concern_z', name: 'Going concern (10-K/Q)', color: COLORS.negative, dash: '4 3' },
    { key: 'forbearance_z', name: 'Forbearance (8-K)', color: COLORS.chartSecondary, dash: '4 3' },
    { key: 'rsa_z', name: 'RSA (8-K)', color: COLORS.chart6mo, dash: '4 3' },
    { key: 'chapter11_z', name: 'Chapter 11 (8-K)', color: COLORS.textSecondary, dash: '2 3' },
  ];
  return (
    <div style={{ marginTop: 14, borderTop: `1px solid ${COLORS.border}`, paddingTop: 10 }}>
      <div className="muted" style={{ fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase', marginBottom: 6 }}>
        SEC filings distress language — quarterly since 2001 {meth && <DocLink m={meth} />}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginBottom: 8 }}>
        <Tile label={`Filings distress (z) · ${last.date.slice(0, 7)}`} value={z(comp)} color={color}
          sub={`4q change ${z(chg)}`} />
        <Tile label="Going-concern share" value={last.going_concern_share == null ? '—'
          : `${(last.going_concern_share * 100).toFixed(1)}%`} sub="of 10-K/10-Q filings" />
        <Tile label="RSA / Ch.11 (z)" value={`${z(last.rsa_z)} / ${z(last.chapter11_z)}`}
          sub="pre-pack deals / petitions in 8-Ks" />
      </div>
      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={COLORS.border} strokeDasharray="2 4" />
          <XAxis dataKey="date" tick={{ fill: COLORS.textSecondary, fontSize: 10 }} stroke={COLORS.border}
            tickFormatter={(d: string) => d.slice(0, 4)} minTickGap={40} />
          <YAxis tick={{ fill: COLORS.textSecondary, fontSize: 10 }} stroke={COLORS.border} width={36} />
          <ReferenceLine y={0} stroke={COLORS.borderBright} />
          <Tooltip content={({ active, payload, label }) =>
            active && payload?.length ? (
              <TooltipShell title={label}>
                {payload.map((p) => (
                  <div key={String(p.dataKey)} style={{ color: p.color }}>{p.name}: {z(Number(p.value))}</div>
                ))}
              </TooltipShell>
            ) : null} />
          {lines.map((l) => (
            <Line key={String(l.key)} dataKey={l.key as string} name={l.name} stroke={l.color}
              strokeDasharray={l.dash} dot={false} strokeWidth={l.key === 'composite_z' ? 1.8 : 1}
              isAnimationActive={false} connectNulls />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
