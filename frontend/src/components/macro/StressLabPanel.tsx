// Stress Lab — set a macro shock, watch the credit views respond.
//
// Sliders feed the client-side stress engine (lib/stressEngine.ts), which
// re-runs macrobot's transmission on the bundle's `stress_kit`: policy reaction
// → curve → spreads and losses → stance, 12m total return and every path.
// Grey = baseline forecast, colour = your scenario.

import { useMemo, useState } from 'react';
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
import {
  defaultInputs,
  stressResponse,
  verifyKit,
  type StressInputs,
  type StressKit,
} from '../../lib/stressEngine';
import type { MacroViews } from '../../lib/types';
import Panel from '../shared/Panel';
import TooltipShell from '../charts/TooltipShell';
import DocLink from './DocLink';

const STANCE: Record<string, { label: string; color: string }> = {
  risk_on: { label: 'RISK-ON', color: COLORS.positive },
  neutral: { label: 'NEUTRAL', color: COLORS.neutral },
  risk_off: { label: 'RISK-OFF', color: COLORS.negative },
  unknown: { label: '—', color: COLORS.textDim },
};
const PHASES = [{ m: 1, label: 'Immediate' }, { m: 6, label: '6 months' }, { m: 12, label: '12 months' }];

const signed = (x: number, dp = 0, unit = '') => `${x >= 0 ? '+' : ''}${x.toFixed(dp)}${unit}`;
const pct = (x: number | undefined, dp = 2) => (x == null ? '—' : `${x.toFixed(dp)}%`);

// ── controls ──────────────────────────────────────────────────

function Slider({ spec, value, onChange }: {
  spec: StressKit['inputs'][number]; value: number; onChange: (v: number) => void;
}) {
  const moved = Math.abs(value - spec.default) > 1e-9;
  const shown = spec.unit === '×' ? `${value.toFixed(1)}×`
    : `${value > 0 && spec.default === 0 ? '+' : ''}${Number.isInteger(spec.step) ? value.toFixed(0) : value.toFixed(2)}${spec.unit === 'pp' || spec.unit === 'bp' || spec.unit === '%' || spec.unit === 'σ' ? spec.unit : ` ${spec.unit}`}`;
  return (
    <label style={{ display: 'block', marginBottom: 10 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11 }}>
        <span style={{ color: moved ? COLORS.textPrimary : COLORS.textSecondary }}>{spec.label}</span>
        <span style={{ color: moved ? COLORS.accent : COLORS.textDim, fontVariantNumeric: 'tabular-nums' }}>{shown}</span>
      </div>
      <input type="range" min={spec.min} max={spec.max} step={spec.step} value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        onDoubleClick={() => onChange(spec.default)}
        style={{ width: '100%', accentColor: COLORS.accent }} />
    </label>
  );
}

// ── outputs ───────────────────────────────────────────────────

function Compare({ label, base, stressed, good = 'up', sub }: {
  label: string; base: string; stressed: string; good?: 'up' | 'down' | 'none'; sub?: string;
}) {
  const b = parseFloat(base);
  const s = parseFloat(stressed);
  const changed = base !== stressed;
  const better = good === 'none' ? null : good === 'up' ? s > b : s < b;
  const color = !changed || better == null ? COLORS.textPrimary : better ? COLORS.positive : COLORS.negative;
  return (
    <div style={{ border: `1px solid ${COLORS.border}`, padding: '8px 12px', flex: '1 1 150px', background: COLORS.bgPanel }}>
      <div className="muted" style={{ fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase' }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700, marginTop: 2, color }}>{stressed}</div>
      <div className="muted" style={{ fontSize: 10, marginTop: 2 }}>baseline {base}{sub ? ` · ${sub}` : ''}</div>
    </div>
  );
}

function PathChart({ title, h, base, stressed, band, color, fmt }: {
  title: string; h: number[]; base: number[] | null | undefined; stressed: number[] | null | undefined;
  band?: { lo: number[]; hi: number[] } | null; color: string; fmt: (v: number) => string;
}) {
  if (!base || !stressed) return null;
  const rows = h.map((m, i) => ({
    h: m === 0 ? 'now' : `+${m}m`,
    band: band ? [band.lo[i], band.hi[i]] : undefined,
    base: base[i],
    stressed: stressed[i],
  }));
  const end = stressed[stressed.length - 1] - base[base.length - 1];
  return (
    <div style={{ border: `1px solid ${COLORS.border}`, padding: '8px 10px', background: COLORS.bgPanel }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10 }}>
        <span className="muted" style={{ letterSpacing: '0.1em', textTransform: 'uppercase' }}>{title}</span>
        <span style={{ color: Math.abs(end) < 1e-9 ? COLORS.textDim : color }}>
          12m: {fmt(stressed[stressed.length - 1])} (base {fmt(base[base.length - 1])})
        </span>
      </div>
      <ResponsiveContainer width="100%" height={150}>
        <ComposedChart data={rows} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={COLORS.border} vertical={false} />
          <XAxis dataKey="h" tick={{ fill: COLORS.axis, fontSize: 9 }} stroke={COLORS.axis} interval={2} />
          <YAxis tick={{ fill: COLORS.axis, fontSize: 9 }} stroke={COLORS.axis} width={44}
            tickFormatter={fmt} domain={['auto', 'auto']} />
          <Tooltip content={({ active, payload, label }) => {
            if (!active || !payload?.length) return null;
            const r = payload[0].payload as (typeof rows)[number];
            return (
              <TooltipShell title={label}>
                <div style={{ color }}>scenario {fmt(r.stressed)}</div>
                <div style={{ color: COLORS.textSecondary }}>baseline {fmt(r.base)}</div>
              </TooltipShell>
            );
          }} />
          {band && <Area dataKey="band" stroke="none" fill={COLORS.textSecondary} fillOpacity={0.12} isAnimationActive={false} />}
          <Line dataKey="base" stroke={COLORS.textSecondary} strokeDasharray="5 4" dot={false} strokeWidth={1.4} isAnimationActive={false} />
          <Line dataKey="stressed" stroke={color} dot={false} strokeWidth={2.4} isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

// ── panel ─────────────────────────────────────────────────────

// `initialPreset` lets a link open the lab on a named scenario
// (/forecasts?view=stress&preset=recession).
export default function StressLabPanel({ views, initialPreset }: {
  views: MacroViews; initialPreset?: string | null;
}) {
  const kit: StressKit | undefined = views.stress_kit;
  const start = kit?.presets.find((p) => p.name === initialPreset);
  const [inputs, setInputs] = useState<StressInputs>(
    () => (kit ? { ...defaultInputs(kit), ...(start?.inputs ?? {}) } : {}));
  const [phase, setPhase] = useState(6);
  const [preset, setPreset] = useState<string | null>(start?.name ?? 'baseline');

  const check = useMemo(() => (kit ? verifyKit(kit) : null), [kit]);
  const base = useMemo(() => (kit ? stressResponse(kit, {}, phase) : null), [kit, phase]);
  const res = useMemo(() => (kit ? stressResponse(kit, inputs, phase) : null), [kit, inputs, phase]);

  if (!kit || !base || !res) {
    return (
      <Panel title="Stress Lab">
        <div className="muted" style={{ fontSize: 11 }}>
          The forecast bundle has no stress kit yet — re-run macrobot&apos;s update_forecasts and run_synthesis.
        </div>
      </Panel>
    );
  }

  const set = (key: string, v: number) => { setInputs((p) => ({ ...p, [key]: v })); setPreset(null); };
  const load = (name: string, vals: StressInputs) => { setInputs({ ...defaultInputs(kit), ...vals }); setPreset(name); };
  const d = res.deltas;
  const st = STANCE[res.stance.stance] ?? STANCE.unknown;
  const stBase = STANCE[base.stance.stance] ?? STANCE.unknown;
  const P = res.paths;
  const B = base.paths;
  const last = (a?: number[] | null) => (a ? a[a.length - 1] : undefined);
  const meth = views.methodology?.stress_lab;
  const macroSpecs = kit.inputs.filter((s) => !['vol_mult', 'funding_z', 'ebitda_pct'].includes(s.key));
  const overlaySpecs = kit.inputs.filter((s) => ['vol_mult', 'funding_z', 'ebitda_pct'].includes(s.key));
  const chain: [string, string][] = [
    ['Fed funds', signed(d.d_policy_bp, 0, 'bp')],
    ['10y', signed(d.d_10y_bp, 0, 'bp')],
    ['Unemployment', signed(d.d_unrate_pp, 1, 'pp')],
    ['HY OAS', signed(d.d_hy_oas_pp * 100, 0, 'bp')],
    ['IG OAS', signed(d.d_ig_oas_pp * 100, 0, 'bp')],
    ['HY fair value', signed(d.d_hy_fair_value_pp * 100, 0, 'bp')],
    ['Corp losses', signed(d.d_corp_loss_pp, 2, 'pp')],
    ['Card losses', signed(d.d_card_loss_pp, 2, 'pp')],
    ['Rates vol', `×${d.vol_mult.toFixed(2)}`],
    ['Funding stress', signed(d.d_funding_z, 2, 'σ')],
  ];

  return (
    <Panel
      title="Stress Lab"
      subtitle={
        <span>
          set a macro shock → policy &amp; curve → spreads &amp; losses → stance, total return and paths · grey = baseline
          {meth && <> <DocLink m={meth} /></>}
          {check && (
            <span style={{ marginLeft: 10, color: check.failed.length ? COLORS.negative : COLORS.textDim }}>
              {check.failed.length
                ? `engine check FAILED on: ${check.failed.join(', ')}`
                : `engine check ${check.passed}/${check.total} presets match macrobot`}
            </span>
          )}
        </span>
      }
    >
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(260px, 320px) minmax(0, 1fr)', gap: 16, alignItems: 'start' }}>
        {/* controls */}
        <div style={{ border: `1px solid ${COLORS.border}`, padding: 12, background: COLORS.bgPanel, position: 'sticky', top: 8 }}>
          <div className="muted" style={{ fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase', marginBottom: 6 }}>Presets</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 12 }}>
            {kit.presets.map((p) => (
              <button key={p.name} type="button" title={p.label} onClick={() => load(p.name, p.inputs)}
                style={{ fontFamily: 'inherit', fontSize: 10, padding: '3px 8px', cursor: 'pointer', textTransform: 'uppercase',
                  background: 'transparent', color: preset === p.name ? COLORS.accent : COLORS.textSecondary,
                  border: `1px solid ${preset === p.name ? COLORS.accent : COLORS.borderBright}` }}>
                {p.name.replace(/_/g, ' ')}
              </button>
            ))}
          </div>
          <div className="muted" style={{ fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase', marginBottom: 6 }}>Macro shock (over 12 months)</div>
          {macroSpecs.map((s) => <Slider key={s.key} spec={s} value={inputs[s.key] ?? s.default} onChange={(v) => set(s.key, v)} />)}
          <div className="muted" style={{ fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase', margin: '4px 0 6px' }}>Market &amp; borrower overlays</div>
          {overlaySpecs.map((s) => <Slider key={s.key} spec={s} value={inputs[s.key] ?? s.default} onChange={(v) => set(s.key, v)} />)}
          <div className="muted" style={{ fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase', margin: '4px 0 6px' }}>Markets reprice over</div>
          <div style={{ display: 'flex', gap: 4 }}>
            {PHASES.map((p) => (
              <button key={p.m} type="button" onClick={() => setPhase(p.m)}
                style={{ flex: 1, fontFamily: 'inherit', fontSize: 10, padding: '3px 6px', cursor: 'pointer',
                  background: 'transparent', color: phase === p.m ? COLORS.accent : COLORS.textSecondary,
                  border: `1px solid ${phase === p.m ? COLORS.accent : COLORS.borderBright}` }}>
                {p.label}
              </button>
            ))}
          </div>
          <div className="muted" style={{ fontSize: 10, marginTop: 10 }}>Double-click a slider to reset it.</div>
        </div>

        {/* results */}
        <div className="stack">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            <div style={{ border: `1px solid ${COLORS.border}`, padding: '8px 12px', flex: '1 1 170px', background: COLORS.bgPanel }}>
              <div className="muted" style={{ fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase' }}>Credit stance after the shock</div>
              <div style={{ fontSize: 20, fontWeight: 700, marginTop: 2, color: st.color }}>{st.label}</div>
              <div className="muted" style={{ fontSize: 10, marginTop: 2 }}>
                baseline {stBase.label} · {res.stance.valuation} · losses {res.stance.loss_cycle} · vol {res.stance.volatility} · funding {res.stance.funding}
              </div>
            </div>
            {(['hy', 'ig'] as const).map((tag) => res.total_return[tag] && (
              <Compare key={tag} label={`${tag.toUpperCase()} 12m total return`}
                base={pct(base.total_return[tag].total_return)} stressed={pct(res.total_return[tag].total_return)}
                sub={`rate ${signed(res.total_return[tag].rate_pnl, 1)} · spread ${signed(res.total_return[tag].spread_pnl, 1)} · loss ${signed(-res.total_return[tag].expected_loss, 1)}`} />
            ))}
            {P.share_icr_lt_1 && B.share_icr_lt_1 && (
              <Compare label="Borrowers ICR < 1× in 12m" good="down"
                base={pct(last(B.share_icr_lt_1)! * 100, 1)} stressed={pct(last(P.share_icr_lt_1)! * 100, 1)}
                sub={`coupon ${pct(last(P.coupon))}`} />
            )}
          </div>

          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 16px', fontSize: 11, border: `1px solid ${COLORS.border}`, padding: '6px 10px', background: COLORS.bgPanel }}>
            <span className="muted" style={{ fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase', alignSelf: 'center' }}>12m change vs baseline</span>
            {chain.map(([k, v]) => (
              <span key={k}><span className="muted">{k}</span>{' '}
                <span style={{ color: /^([+-]0(\.0+)?(bp|pp|σ)|×1\.00)$/.test(v) ? COLORS.textDim : COLORS.textPrimary }}>{v}</span></span>
            ))}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 10 }}>
            <PathChart title="HY OAS (bp)" h={P.h} color={COLORS.negative} fmt={(v) => `${Math.round(v * 100)}`}
              base={B.hy?.median} stressed={P.hy?.median} band={B.hy ? { lo: B.hy.p16, hi: B.hy.p84 } : null} />
            <PathChart title="IG OAS (bp)" h={P.h} color={COLORS.chartSecondary} fmt={(v) => `${Math.round(v * 100)}`}
              base={B.ig?.median} stressed={P.ig?.median} band={B.ig ? { lo: B.ig.p16, hi: B.ig.p84 } : null} />
            <PathChart title="Corporate loss rate (%)" h={P.h} color={COLORS.accent} fmt={(v) => v.toFixed(2)}
              base={B.corp_loss} stressed={P.corp_loss} />
            <PathChart title="Card charge-offs (%)" h={P.h} color={COLORS.accent} fmt={(v) => v.toFixed(2)}
              base={B.card_loss} stressed={P.card_loss} />
            <PathChart title="Fed funds (%)" h={P.h} color={COLORS.chartTertiary} fmt={(v) => v.toFixed(2)}
              base={B.policy} stressed={P.policy} />
            <PathChart title="10-year Treasury (%)" h={P.h} color={COLORS.chartTertiary} fmt={(v) => v.toFixed(2)}
              base={B.ust10} stressed={P.ust10} />
            <PathChart title="Direct-lending coupon (%)" h={P.h} color={COLORS.chart6mo} fmt={(v) => v.toFixed(2)}
              base={B.coupon} stressed={P.coupon} />
            <PathChart title="Borrowers with ICR < 1× (%)" h={P.h} color={COLORS.chart6mo} fmt={(v) => (v * 100).toFixed(1)}
              base={B.share_icr_lt_1} stressed={P.share_icr_lt_1} />
          </div>
        </div>
      </div>
    </Panel>
  );
}
