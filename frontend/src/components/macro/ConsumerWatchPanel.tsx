// Consumer Watch — what households expect and what card books already show,
// and whether each has led card charge-offs before (macrobot's
// `consumer_early_warning` model: NY Fed SCE + large-bank card data).

import { COLORS } from '../../lib/colors';
import type { MacroViews } from '../../lib/types';
import Panel from '../shared/Panel';

export interface WatchIndicator {
  key: string; label: string; group: string; unit: string; sign: number;
  level: number | null; z: number | null; chg4q: number | null; lead_q: number;
  corr: number | null; incr_r2: number | null; n: number; leading: boolean;
  implied: number | null; history: number[];
}
export interface ConsumerWatch {
  as_of: string | null;
  target: { label: string; level: number | null; chg4q: number | null };
  composite_z: number | null; implied_median: number | null; min_incr_r2: number;
  indicators: WatchIndicator[];
  segments: {
    topics: { key: string; label: string }[];
    groups: { key: string; label: string }[];
    cells: Record<string, { level: number | null; z: number | null; chg12: number | null }>;
  };
}

const signed = (x: number, dp = 1, unit = '') => `${x >= 0 ? '+' : ''}${x.toFixed(dp)}${unit}`;
const head = (t: string) => (
  <div className="muted" style={{ fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase' }}>{t}</div>
);
// stress colour for a signed z (positive = more stress)
const zColor = (z: number | null) => (z == null ? COLORS.textSecondary : z >= 1 ? COLORS.negative : z >= 0.5 ? COLORS.accent : z <= -0.5 ? COLORS.positive : COLORS.textPrimary);

function Spark({ values, color }: { values: number[]; color: string }) {
  if (values.length < 2) return null;
  const w = 150; const h = 30;
  const lo = Math.min(...values); const hi = Math.max(...values); const span = hi - lo || 1;
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * w},${h - 2 - ((v - lo) / span) * (h - 4)}`).join(' ');
  const mean = values.reduce((a, b) => a + b, 0) / values.length;
  const my = h - 2 - ((mean - lo) / span) * (h - 4);
  return (
    <svg viewBox={`0 0 ${w} ${h}`} width="100%" height={h} preserveAspectRatio="none" aria-hidden>
      <line x1={0} x2={w} y1={my} y2={my} stroke={COLORS.border} strokeDasharray="3 3" />
      <polyline points={pts} fill="none" stroke={color} strokeWidth={1.6} />
    </svg>
  );
}

function IndicatorTile({ i }: { i: WatchIndicator }) {
  const color = zColor(i.z);
  const dp = i.unit === 'pts' ? 0 : 1;
  const unit = i.unit === 'pts' ? '' : i.unit;
  return (
    <div style={{ border: `1px solid ${i.leading ? COLORS.borderBright : COLORS.border}`, padding: '8px 12px', background: COLORS.bgPanel, opacity: i.leading ? 1 : 0.7 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 6, alignItems: 'baseline' }}>
        <span className="muted" style={{ fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase' }}>{i.label}</span>
        <span style={{ fontSize: 9, letterSpacing: '0.08em', color: i.leading ? COLORS.accent : COLORS.textDim, whiteSpace: 'nowrap' }}>
          {i.leading ? `LED BY ~${i.lead_q}Q` : 'NO LEAD'}
        </span>
      </div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginTop: 2 }}>
        <span style={{ fontSize: 20, fontWeight: 700, color }}>{i.level == null ? '—' : `${i.level.toFixed(dp)}${unit}`}</span>
        <span style={{ fontSize: 10, color: COLORS.textSecondary }}>
          {i.chg4q != null && `${signed(i.chg4q, dp, unit === '%' ? 'pp' : '')} vs a year ago`}
        </span>
      </div>
      <Spark values={i.history} color={color} />
      <div className="muted" style={{ fontSize: 9, lineHeight: 1.4 }}>
        {i.z != null && <span style={{ color }}>{signed(i.z, 1, 'σ')} stress vs own history{i.sign < 0 ? ' (lower = worse)' : ''}</span>}
        {' · '}adds {(Math.max(i.incr_r2 ?? 0, 0) * 100).toFixed(0)}% explained variance {i.lead_q}q ahead (n={i.n})
      </div>
    </div>
  );
}

function SegmentHeat({ w }: { w: ConsumerWatch }) {
  const { topics, groups, cells } = w.segments;
  if (!topics.length) return null;
  return (
    <div style={{ display: 'grid', gridTemplateColumns: `minmax(150px, 1.5fr) repeat(${groups.length}, minmax(52px, 1fr))`, gap: 2, overflowX: 'auto' }}>
      <span />
      {groups.map((g) => (
        <div key={g.key} className="muted" style={{ fontSize: 8, letterSpacing: '0.06em', textAlign: 'center', textTransform: 'uppercase', alignSelf: 'end' }}>{g.label}</div>
      ))}
      {topics.map((t) => {
        // income growth: lower = worse, so flip the sign for colour
        const flip = t.key === 'HHINC' ? -1 : 1;
        return (
          <div key={t.key} style={{ display: 'contents' }}>
            <span style={{ fontSize: 11, color: COLORS.textSecondary, alignSelf: 'center' }}>{t.label}</span>
            {groups.map((g) => {
              const c = cells[`${t.key}_${g.key}`];
              if (!c || c.level == null) return <div key={g.key} />;
              const z = (c.z ?? 0) * flip;
              const tone = z >= 0 ? COLORS.negative : COLORS.positive;
              const strength = Math.min(Math.abs(z) / 2.5, 1);
              return (
                <div key={g.key} title={`${t.label} · ${g.label}: ${c.level.toFixed(1)}% (3-month average), ${signed(c.z ?? 0, 1, 'σ')} vs this group\'s own history, ${signed(c.chg12 ?? 0, 1, 'pp')} vs a year ago`}
                  style={{ textAlign: 'center', padding: '5px 2px', border: `1px solid ${COLORS.border}`, fontVariantNumeric: 'tabular-nums',
                    background: `color-mix(in srgb, ${tone} ${Math.round(strength * 50)}%, ${COLORS.bgPanel})` }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: COLORS.textPrimary }}>{c.level.toFixed(1)}</div>
                  <div style={{ fontSize: 8, color: COLORS.textSecondary }}>{signed(c.z ?? 0, 1, 'σ')}</div>
                </div>
              );
            })}
          </div>
        );
      })}
    </div>
  );
}

export default function ConsumerWatchPanel({ views }: { views: MacroViews }) {
  const w = views.consumer_watch;
  if (!w || !w.indicators.length) {
    return (
      <Panel title="Consumer Watch">
        <div className="muted" style={{ fontSize: 11 }}>No consumer early-warning data in this bundle — run macrobot&apos;s consumer_early_warning model.</div>
      </Panel>
    );
  }
  const groups = Array.from(new Set(w.indicators.map((i) => i.group)));
  const leaders = w.indicators.filter((i) => i.leading);
  const cz = w.composite_z;
  return (
    <Panel title="Consumer Watch" subtitle="What households expect and what card books show — and whether each has led card charge-offs before">
      <div className="stack">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))', gap: 10 }}>
          <div style={{ border: `1px solid ${COLORS.border}`, padding: '8px 12px', background: COLORS.bgPanel }}>
            {head('Leading-indicator stress')}
            <div style={{ fontSize: 24, fontWeight: 700, color: zColor(cz) }}>{cz == null ? '—' : signed(cz, 1, 'σ')}</div>
            <div className="muted" style={{ fontSize: 10 }}>average of the {leaders.length} indicators that have led · 0 = their normal</div>
          </div>
          <div style={{ border: `1px solid ${COLORS.border}`, padding: '8px 12px', background: COLORS.bgPanel }}>
            {head(w.target.label)}
            <div style={{ fontSize: 24, fontWeight: 700, color: COLORS.textPrimary }}>{w.target.level == null ? '—' : `${w.target.level.toFixed(2)}%`}</div>
            <div className="muted" style={{ fontSize: 10 }}>{w.target.chg4q != null && `${signed(w.target.chg4q, 2, 'pp')} vs a year ago`} · all banks</div>
          </div>
          <div style={{ border: `1px solid ${COLORS.border}`, padding: '8px 12px', background: COLORS.bgPanel }}>
            {head('Implied by the leaders')}
            <div style={{ fontSize: 24, fontWeight: 700, color: (w.implied_median ?? 0) > 0.1 ? COLORS.negative : (w.implied_median ?? 0) < -0.1 ? COLORS.positive : COLORS.textPrimary }}>
              {w.implied_median == null ? '—' : signed(w.implied_median, 2, 'pp')}
            </div>
            <div className="muted" style={{ fontSize: 10 }}>year-on-year change in the charge-off rate about a year out · median across leaders</div>
          </div>
        </div>
        {groups.map((g) => (
          <div key={g} className="stack">
            {head(g === 'households' ? 'Households · NY Fed Survey of Consumer Expectations' : 'Card books · large banks (FR Y-14M)')}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))', gap: 10 }}>
              {w.indicators.filter((i) => i.group === g).map((i) => <IndicatorTile key={i.key} i={i} />)}
            </div>
          </div>
        ))}
        {head('Expectations by group · latest 3-month average (%) · colour = how unusual for that group')}
        <SegmentHeat w={w} />
        <div className="muted" style={{ fontSize: 10, border: `1px solid ${COLORS.border}`, padding: '6px 10px', background: COLORS.bgPanel, lineHeight: 1.5 }}>
          &quot;Led by ~Nq&quot; means the indicator&apos;s year-on-year change added at least {(w.min_incr_r2 * 100).toFixed(0)}% of explained variance to card charge-offs N quarters later, beyond
          charge-offs&apos; own momentum, with the expected sign. The evidence is thin: about forty quarters and one loss upswing (2022-24), with the pandemic quarters left out.
          Dimmed tiles did not lead. Group readings rest on a few hundred respondents each and are noisy; compare a group with its own history, not with other groups.
        </div>
      </div>
    </Panel>
  );
}
