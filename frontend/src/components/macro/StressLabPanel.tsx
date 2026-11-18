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
  type AbfClass,
  type AbfStructure,
  type AbfStructureResult,
  type StressInputs,
  type StressKit,
  type StressResult,
} from '../../lib/stressEngine';
import type { MacroViews } from '../../lib/types';
import Panel from '../shared/Panel';
import TooltipShell from '../charts/TooltipShell';
import ConsumerSegmentsView from './ConsumerSegmentsView';
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

// What each input feeds, shown under its slider so none looks dead.
const MOVES: Record<string, string> = {
  core_pce_pp: 'Fed funds, 10y, coupon → unemployment → spreads, losses, EBITDA',
  unrate_pp: 'spreads, corporate and card losses, borrower EBITDA',
  vix: 'spreads, rates vol, funding stress',
  sloos: 'corporate and card losses',
  term_premium_bp: '10y, curve slope → spreads',
  policy_bp: 'Fed funds, 10y, coupon → unemployment → spreads, losses, EBITDA',
  vol_mult: 'spreads, 10y range',
  funding_z: 'spreads',
  ebitda_pct: 'borrower coverage (ICR), on top of the unemployment effect',
  hpi_pct: 'home prices → mortgage losses (ABF view)',
  car_pct: 'used-car prices → auto delinquencies (ABF view)',
  oil_pct: 'oil and gas prices → PDP cash flow (ABF view)',
};
const OVERLAY_KEYS = ['vol_mult', 'funding_z', 'ebitda_pct'];
const ABF_KEYS = ['hpi_pct', 'car_pct', 'oil_pct'];
const REGRESSOR_LABEL: Record<string, string> = {
  d_u: 'unemployment', d_vix: 'VIX', sloos_card: 'bank tightening', sloos_ci: 'bank tightening',
  p_hpi: 'home prices', p_car: 'used-car prices',
};

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
      {MOVES[spec.key] && <div className="muted" style={{ fontSize: 9, marginTop: -2 }}>moves {MOVES[spec.key]}</div>}
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

function PathChart({ title, h, base, stressed, band, stressedBand, color, fmt }: {
  title: string; h: number[]; base: number[] | null | undefined; stressed: number[] | null | undefined;
  band?: { lo: number[]; hi: number[] } | null; stressedBand?: { lo: number[]; hi: number[] } | null;
  color: string; fmt: (v: number) => string;
}) {
  if (!base || !stressed) return null;
  const rows = h.map((m, i) => ({
    h: m === 0 ? 'now' : `+${m}m`,
    band: band ? [band.lo[i], band.hi[i]] : undefined,
    sband: stressedBand ? [stressedBand.lo[i], stressedBand.hi[i]] : undefined,
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
                <div style={{ color }}>scenario {fmt(r.stressed)}{r.sband ? ` (${fmt(r.sband[0])}–${fmt(r.sband[1])})` : ''}</div>
                <div style={{ color: COLORS.textSecondary }}>baseline {fmt(r.base)}{r.band ? ` (${fmt(r.band[0])}–${fmt(r.band[1])})` : ''}</div>
              </TooltipShell>
            );
          }} />
          {band && <Area dataKey="band" stroke="none" fill={COLORS.textSecondary} fillOpacity={0.12} isAnimationActive={false} />}
          {stressedBand && <Area dataKey="sband" stroke="none" fill={color} fillOpacity={0.14} isAnimationActive={false} />}
          <Line dataKey="base" stroke={COLORS.textSecondary} strokeDasharray="5 4" dot={false} strokeWidth={1.4} isAnimationActive={false} />
          <Line dataKey="stressed" stroke={color} dot={false} strokeWidth={2.4} isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

// ── ABF collateral ────────────────────────────────────────────

function AbfLossTile({ c, r }: { c: AbfClass; r: { delta: number; stressed: number | null } }) {
  const base = c.level;
  const stressed = r.stressed;
  const moved = Math.abs(r.delta) > 1e-9;
  const color = !moved ? COLORS.textPrimary : r.delta > 0 ? COLORS.negative : COLORS.positive;
  const scale = Math.max(base ?? 0, stressed ?? 0, 0.01) * 1.05;
  const mult = base != null && stressed != null && base >= 0.25 ? stressed / base : null;
  const bar = (v: number | null, fill: string) => (
    <div style={{ height: 5, background: COLORS.border, marginTop: 3 }}>
      <div style={{ height: 5, width: `${Math.min(((v ?? 0) / scale) * 100, 100)}%`, background: fill }} />
    </div>
  );
  return (
    <div style={{ border: `1px solid ${COLORS.border}`, padding: '8px 12px', background: COLORS.bgPanel }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <span className="muted" style={{ fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase' }}>{c.label}</span>
        {mult != null && moved && <span style={{ fontSize: 11, color }}>×{mult.toFixed(2)}</span>}
      </div>
      <div style={{ fontSize: 20, fontWeight: 700, marginTop: 2, color }}>{stressed == null ? '—' : `${stressed.toFixed(2)}%`}</div>
      <div className="muted" style={{ fontSize: 10, marginTop: 2 }}>
        baseline {base == null ? '—' : `${base.toFixed(2)}%`} · {signed(r.delta, 2, 'pp')} in 12m
      </div>
      {bar(base, COLORS.textSecondary)}
      {bar(stressed, color === COLORS.textPrimary ? COLORS.textSecondary : color)}
      <div className="muted" style={{ fontSize: 9, marginTop: 6, lineHeight: 1.4 }}>
        {c.measure}
        {c.usable
          ? <> · moves with {Object.keys(c.coefs).map((k) => REGRESSOR_LABEL[k] ?? k).join(', ')} · R² {c.r2.toFixed(2)}</>
          : <span style={{ color: COLORS.accent }}> · no measurable link to the macro shock (R² {c.r2.toFixed(2)}) — held at baseline</span>}
        {c.relevance && <><br />for: {c.relevance}</>}
      </div>
    </div>
  );
}

function AbfDriverTile({ c, r }: { c: AbfClass; r: { delta: number } }) {
  const moved = Math.abs(r.delta) > 1e-9;
  const color = !moved ? COLORS.textPrimary : r.delta < 0 ? COLORS.negative : COLORS.positive;
  return (
    <div style={{ border: `1px solid ${COLORS.border}`, padding: '8px 12px', background: COLORS.bgPanel }}>
      <div className="muted" style={{ fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase' }}>{c.label}</div>
      <div style={{ fontSize: 20, fontWeight: 700, marginTop: 2, color }}>{signed(r.delta, 1, '%')}</div>
      <div className="muted" style={{ fontSize: 9, marginTop: 4, lineHeight: 1.4 }}>
        {c.measure} · R² {c.r2.toFixed(2)}
        {c.relevance && <><br />for: {c.relevance}</>}
      </div>
    </div>
  );
}

function AbfView({ kit, res }: { kit: StressKit; res: StressResult }) {
  const abf = kit.abf;
  if (!abf) {
    return <div className="muted" style={{ fontSize: 11 }}>No ABF collateral estimates in this bundle — run macrobot&apos;s abf_collateral model.</div>;
  }
  const losses = abf.classes.filter((c) => c.kind === 'loss');
  const drivers = abf.classes.filter((c) => c.kind === 'driver');
  const head = (t: string) => (
    <div className="muted" style={{ fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase' }}>{t}</div>
  );
  return (
    <div className="stack">
      {head('Loss and delinquency rates by asset class · latest reading → 12 months under your scenario')}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))', gap: 10 }}>
        {losses.map((c) => res.abf[c.key] && <AbfLossTile key={c.key} c={c} r={res.abf[c.key]} />)}
      </div>
      {head('Collateral values and cash-flow drivers · 12-month change vs baseline')}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: 10 }}>
        {drivers.map((c) => res.abf[c.key] && <AbfDriverTile key={c.key} c={c} r={res.abf[c.key]} />)}
      </div>
      <div className="muted" style={{ fontSize: 10, border: `1px solid ${COLORS.border}`, padding: '6px 10px', background: COLORS.bgPanel, lineHeight: 1.5 }}>
        Bank and credit-bureau aggregates, not securitized pools, and no deal structure yet. Not modelled:{' '}
        {abf.not_modelled.map((m, i) => (
          <span key={m.label}>{i > 0 && ' · '}<span style={{ color: COLORS.textSecondary }}>{m.label}</span> ({m.reason})</span>
        ))}
      </div>
    </div>
  );
}

const STATUS_COLOR: Record<string, string> = {
  intact: COLORS.positive, eroding: COLORS.accent, 'at risk': COLORS.negative, loss: COLORS.negative,
};

function StructureCard({ s, r }: { s: AbfStructure; r: AbfStructureResult }) {
  const dscr = r.kind === 'dscr';
  const head = dscr
    ? `prices ${signed(r.price_pct ?? 0, 0, '%')} → cash flow ${signed(r.cash_flow_pct ?? 0, 0, '%')}`
    : `pool loss ${s.base_loss_pct.toFixed(1)}% → ${(r.stressed_loss_pct ?? 0).toFixed(1)}%${s.revolving ? ' a year' : ' lifetime'} (×${(r.life_multiple ?? 1).toFixed(2)})`;
  return (
    <div style={{ border: `1px solid ${COLORS.border}`, padding: '8px 12px', background: COLORS.bgPanel }}>
      <div className="muted" style={{ fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase' }}>{s.label}</div>
      <div style={{ fontSize: 11, margin: '2px 0 8px', color: COLORS.textPrimary }}>{head}</div>
      {r.tranches.map((t) => {
        const color = STATUS_COLOR[t.status] ?? COLORS.textSecondary;
        const used = Math.max(t.used, 0);
        return (
          <div key={t.rating} title={`${dscr ? 'DSCR' : 'loss multiple'} ${t.base.toFixed(2)}× → ${t.stressed.toFixed(2)}×`}
            style={{ display: 'grid', gridTemplateColumns: '34px 1fr 96px', gap: 8, alignItems: 'center', fontSize: 10, marginBottom: 4 }}>
            <span style={{ color: COLORS.textSecondary }}>{t.rating}</span>
            <div style={{ height: 7, background: COLORS.border, position: 'relative' }}>
              <div style={{ height: 7, width: `${Math.min(used, 1) * 100}%`, background: color }} />
            </div>
            <span style={{ color: used < 0.005 ? COLORS.textDim : color, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
              {(used * 100).toFixed(0)}% · {t.status}
            </span>
          </div>
        );
      })}
      <div className="muted" style={{ fontSize: 9, marginTop: 6, lineHeight: 1.4 }}>
        {dscr
          ? `DSCR ${r.tranches.map((t) => `${t.rating} ${t.base.toFixed(2)}→${t.stressed.toFixed(2)}×`).join(' · ')} · cash trap ${s.cash_trap_dscr.toFixed(2)}×`
          : `cushion ${r.tranches.map((t) => `${t.rating} ${t.base.toFixed(1)}→${t.stressed.toFixed(1)}×`).join(' · ')}`}
        {s.note && <><br />{s.note}</>}
      </div>
    </div>
  );
}

function StructuresView({ kit, res }: { kit: StressKit; res: StressResult }) {
  const sb = kit.abf?.structures;
  if (!sb || !res.abf_structures.length) {
    return <div className="muted" style={{ fontSize: 11 }}>No generic structures in this bundle — re-run macrobot&apos;s run_synthesis.</div>;
  }
  const spec = Object.fromEntries(sb.structures.map((s) => [s.key, s]));
  return (
    <div className="stack">
      <div className="muted" style={{ fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase' }}>
        Tranche headroom used by your scenario · 0% = untouched · 100% = first dollar of principal loss
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 10 }}>
        {res.abf_structures.map((r) => spec[r.key] && <StructureCard key={r.key} s={spec[r.key]} r={r} />)}
      </div>
      <div className="muted" style={{ fontSize: 10, border: `1px solid ${COLORS.border}`, padding: '6px 10px', background: COLORS.bgPanel, lineHeight: 1.5 }}>
        Generic deals, not yours: base losses and tranche loss multiples are illustrative assumptions in the range of recent public deals.
        The scenario&apos;s change in each asset class&apos;s loss rate (ABF collateral view) scales the pool&apos;s base loss; elevated losses are
        assumed to last {sb.stress_years.toFixed(0)} years, so short deals take the full hit and long deals part of it.
        Bars turn amber at {(sb.watch_used * 100).toFixed(0)}% and red at {(sb.at_risk_used * 100).toFixed(0)}%. Not a rating forecast.
      </div>
    </div>
  );
}

// ── panel ─────────────────────────────────────────────────────

// `initialPreset` lets a link open the lab on a named scenario
// (/forecasts?view=stress&preset=recession).
export default function StressLabPanel({ views, initialPreset, initialPanel }: {
  views: MacroViews; initialPreset?: string | null; initialPanel?: string | null;
}) {
  const [panel, setPanel] = useState<'markets' | 'abf' | 'structures' | 'consumer'>(
    initialPanel === 'abf' || initialPanel === 'structures' || initialPanel === 'consumer' ? initialPanel : 'markets');
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
  const macroSpecs = kit.inputs.filter((s) => !OVERLAY_KEYS.includes(s.key) && !ABF_KEYS.includes(s.key));
  const overlaySpecs = kit.inputs.filter((s) => OVERLAY_KEYS.includes(s.key));
  const abfSpecs = kit.inputs.filter((s) => ABF_KEYS.includes(s.key));
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
    ['Borrower EBITDA', signed(d.d_ebitda_pct, 1, '%')],
  ];

  return (
    <Panel
      title="Stress Lab"
      subtitle={
        <span>
          set a macro shock → policy &amp; curve → spreads &amp; losses → stance, total return and paths · grey = baseline · shading = 16–84% range (spreads), ±1σ (10y)
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
          {abfSpecs.length > 0 && (
            <>
              <div className="muted" style={{ fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase', margin: '4px 0 6px' }}>ABF collateral prices (on top of unemployment)</div>
              {abfSpecs.map((s) => <Slider key={s.key} spec={s} value={inputs[s.key] ?? s.default} onChange={(v) => set(s.key, v)} />)}
            </>
          )}
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
                <span style={{ color: /^([+-]0(\.0+)?(bp|pp|σ|%)|×1\.00)$/.test(v) ? COLORS.textDim : COLORS.textPrimary }}>{v}</span></span>
            ))}
          </div>

          <div style={{ display: 'flex', gap: 4 }}>
            {([['markets', 'Markets & corporate'], ['abf', 'ABF collateral'], ['structures', 'ABF structures'], ['consumer', 'Consumer segments']] as const).map(([k, label]) => (
              <button key={k} type="button" onClick={() => setPanel(k)}
                style={{ fontFamily: 'inherit', fontSize: 10, padding: '4px 12px', cursor: 'pointer', textTransform: 'uppercase', letterSpacing: '0.1em',
                  background: 'transparent', color: panel === k ? COLORS.accent : COLORS.textSecondary,
                  border: `1px solid ${panel === k ? COLORS.accent : COLORS.borderBright}` }}>
                {label}
              </button>
            ))}
          </div>

          {panel === 'abf' && <AbfView kit={kit} res={res} />}
          {panel === 'structures' && <StructuresView kit={kit} res={res} />}
          {panel === 'consumer' && <ConsumerSegmentsView kit={kit} inputs={inputs} phase={phase} />}

          {panel === 'markets' && <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 10 }}>
            <PathChart title="HY OAS (bp)" h={P.h} color={COLORS.negative} fmt={(v) => `${Math.round(v * 100)}`}
              base={B.hy?.median} stressed={P.hy?.median} band={B.hy ? { lo: B.hy.p16, hi: B.hy.p84 } : null}
              stressedBand={P.hy ? { lo: P.hy.p16, hi: P.hy.p84 } : null} />
            <PathChart title="IG OAS (bp)" h={P.h} color={COLORS.chartSecondary} fmt={(v) => `${Math.round(v * 100)}`}
              base={B.ig?.median} stressed={P.ig?.median} band={B.ig ? { lo: B.ig.p16, hi: B.ig.p84 } : null}
              stressedBand={P.ig ? { lo: P.ig.p16, hi: P.ig.p84 } : null} />
            <PathChart title="Corporate loss rate (%)" h={P.h} color={COLORS.accent} fmt={(v) => v.toFixed(2)}
              base={B.corp_loss} stressed={P.corp_loss} />
            <PathChart title="Card charge-offs (%)" h={P.h} color={COLORS.accent} fmt={(v) => v.toFixed(2)}
              base={B.card_loss} stressed={P.card_loss} />
            <PathChart title="Fed funds (%)" h={P.h} color={COLORS.chartTertiary} fmt={(v) => v.toFixed(2)}
              base={B.policy} stressed={P.policy} />
            <PathChart title="10-year Treasury (%)" h={P.h} color={COLORS.chartTertiary} fmt={(v) => v.toFixed(2)}
              base={B.ust10} stressed={P.ust10} band={B.ust10_band} stressedBand={P.ust10_band} />
            <PathChart title="Direct-lending coupon (%)" h={P.h} color={COLORS.chart6mo} fmt={(v) => v.toFixed(2)}
              base={B.coupon} stressed={P.coupon} />
            <PathChart title="Borrowers with ICR < 1× (%)" h={P.h} color={COLORS.chart6mo} fmt={(v) => (v * 100).toFixed(1)}
              base={B.share_icr_lt_1} stressed={P.share_icr_lt_1} />
          </div>}
        </div>
      </div>
    </Panel>
  );
}
