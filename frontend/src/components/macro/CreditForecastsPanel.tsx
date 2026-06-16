// Credit-desk forecast views: the variables a credit PM actually trades.
// Valuation tiles (OAS vs macro fair value = cheap/rich), spread-path fan charts
// with a fair-value reference line, the default/loss cycle, and vol (MOVE proxy).
// Fed by macrobot's credit_spread / default_cycle / volatility models.

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
import type { MacroViewConcept, MacroViews } from '../../lib/types';
import Panel from '../shared/Panel';
import TooltipShell from '../charts/TooltipShell';
import FredSeriesPanel from './FredSeriesPanel';
import DocLink, { type MethodologyEntry } from './DocLink';

type Methodology = Record<string, MethodologyEntry>;

const HISTORY_RANGES = [
  { label: '3M', years: 0.25 },
  { label: '1Y', years: 1 },
  { label: 'MAX', years: null },
];

const num = (x: number | null | undefined): number | null =>
  x == null || Number.isNaN(x) ? null : x;

// ── valuation tiles ───────────────────────────────────────────

function Tile({ label, value, color, detail, doc }: {
  label: string; value: string; color?: string; detail?: string; doc?: MethodologyEntry;
}) {
  return (
    <div
      style={{
        border: `1px solid ${COLORS.border}`,
        padding: '10px 12px',
        minWidth: 150,
        flex: '1 1 150px',
        background: COLORS.bgPanel,
      }}
    >
      <div className="muted" style={{ fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase',
        display: 'flex', justifyContent: 'space-between', gap: 6 }}>
        <span>{label}</span>
        <DocLink m={doc} />
      </div>
      <div style={{ fontSize: 18, fontWeight: 600, color: color ?? COLORS.textPrimary, marginTop: 2 }}>
        {value}
      </div>
      {detail && <div className="muted" style={{ fontSize: 10, marginTop: 2 }}>{detail}</div>}
    </div>
  );
}

// gap = OAS − macro fair value (pct points). >0 ⇒ spreads wide vs fundamentals ⇒ cheap.
function valuationCall(gapPct: number | null): { word: string; color: string } {
  if (gapPct == null) return { word: '—', color: COLORS.textDim };
  if (gapPct > 0.1) return { word: 'CHEAP', color: COLORS.positive };
  if (gapPct < -0.1) return { word: 'RICH', color: COLORS.negative };
  return { word: 'FAIR', color: COLORS.neutral };
}

// Composite funding-stress z-score → regime label, mirroring the model thresholds.
function fundingCall(z: number | null): { word: string; color: string } {
  if (z == null) return { word: '—', color: COLORS.textDim };
  if (z >= 2) return { word: 'STRESSED', color: COLORS.negative };
  if (z >= 1) return { word: 'ELEVATED', color: COLORS.accent };
  if (z >= 0) return { word: 'NORMAL', color: COLORS.neutral };
  return { word: 'CALM', color: COLORS.positive };
}

function CreditTiles({ ev, carry, meth }: {
  ev: Record<string, number | null>;
  carry?: Record<string, number | null>;
  meth?: Methodology;
}) {
  const pct = (x: number | null, d = 2) => (x == null ? '—' : `${x.toFixed(d)}%`);
  const bp = (x: number | null) => (x == null ? '—' : `${Math.round(x)}bp`);
  const m = meth ?? {};

  const hyGap = num(ev.hy_oas_fairvalue_gap_pct);
  const igGap = num(ev.ig_oas_fairvalue_gap_pct);
  const hyCall = valuationCall(hyGap);
  const igCall = valuationCall(igGap);
  const drChg = num(ev.default_rate_4q_change_pct);

  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
      <Tile
        label="HY OAS"
        value={pct(num(ev.hy_oas_pct))}
        color={hyCall.color}
        doc={m.valuation}
        detail={hyGap == null ? undefined
          : `${hyCall.word} · ${hyGap >= 0 ? '+' : ''}${Math.round(hyGap * 100)}bp vs fair`}
      />
      <Tile
        label="IG OAS"
        value={pct(num(ev.ig_oas_pct))}
        color={igCall.color}
        doc={m.valuation}
        detail={igGap == null ? undefined
          : `${igCall.word} · ${igGap >= 0 ? '+' : ''}${Math.round(igGap * 100)}bp vs fair`}
      />
      <Tile
        label="Default / Loss Rate"
        value={pct(num(ev.default_rate_pct))}
        color={drChg == null ? undefined : drChg > 0 ? COLORS.negative : COLORS.positive}
        doc={m.loss_cycle}
        detail={num(ev.default_rate_4q_fcst_pct) == null ? undefined
          : `4q → ${pct(num(ev.default_rate_4q_fcst_pct))} (${drChg != null && drChg >= 0 ? '+' : ''}${drChg != null ? (drChg * 100).toFixed(0) : '—'}bp)`}
      />
      <Tile
        label="Rates Vol (MOVE-proxy)"
        value={bp(num(ev.rates_vol_bp))}
        doc={m.volatility}
        detail={num(ev.rates_vol_persistence) == null ? undefined
          : `persistence ${num(ev.rates_vol_persistence)!.toFixed(2)}`}
      />
      <Tile
        label="HY Spread Vol"
        value={bp(num(ev.hy_vol_bp))}
        doc={m.volatility}
        detail={num(ev.ig_vol_bp) == null ? undefined : `IG ${bp(num(ev.ig_vol_bp))}`}
      />
      {(() => {
        const z = num(ev.funding_stress_z);
        const call = fundingCall(z);
        return (
          <Tile
            label="Funding Stress"
            value={call.word}
            color={call.color}
            doc={m.funding}
            detail={num(ev.cp_bill_spread_bp) == null ? undefined
              : `CP–bill ${bp(num(ev.cp_bill_spread_bp))} · z ${z != null ? z.toFixed(2) : '—'}`}
          />
        );
      })()}
      {carry && num(carry.hy) != null && (
        <Tile
          label="Carry / Risk (HY)"
          value={num(carry.hy)!.toFixed(1)}
          detail={num(carry.ig) != null
            ? `IG ${num(carry.ig)!.toFixed(1)} · OAS per unit spread-vol`
            : 'OAS per unit spread-vol'}
        />
      )}
    </div>
  );
}

// ── path charts ───────────────────────────────────────────────

function PathTooltip({ active, payload, label, decimals = 2 }: {
  active?: boolean;
  payload?: { name?: string; value?: number | number[]; color?: string }[];
  label?: string | number;
  decimals?: number;
}) {
  if (!active || !payload?.length) return null;
  return (
    <TooltipShell title={String(label)}>
      {payload
        .filter((p) => p.name !== 'BAND' && p.value != null && !Array.isArray(p.value))
        .map((p) => (
          <div key={p.name} style={{ color: p.color, fontSize: 10 }}>
            {p.name}: {(p.value as number).toFixed(decimals)}
          </div>
        ))}
    </TooltipShell>
  );
}

function CreditPathChart({
  concept, color, decimals = 2, fairValue, fairLabel, unitLabel,
}: {
  concept: MacroViewConcept | undefined;
  color: string;
  decimals?: number;
  fairValue?: number | null;
  fairLabel?: string;
  unitLabel?: string;
}) {
  if (!concept || concept.path.length < 2) return null;
  const rows = concept.path.map((p) => ({
    h: p.h_m === 0 ? 'now' : `+${p.h_m}m`,
    band: p.lo != null && p.hi != null ? [p.lo, p.hi] : undefined,
    FORECAST: p.ensemble,
  }));
  return (
    <Panel title={concept.label} subtitle={unitLabel ?? concept.unit}>
      <ResponsiveContainer width="100%" height={190}>
        <ComposedChart data={rows} margin={{ top: 6, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={COLORS.border} vertical={false} />
          <XAxis dataKey="h" tick={{ fill: COLORS.axis, fontSize: 10 }} stroke={COLORS.axis} />
          <YAxis
            tick={{ fill: COLORS.axis, fontSize: 10 }}
            tickFormatter={(v) => v.toFixed(decimals === 0 ? 0 : 1)}
            width={40}
            stroke={COLORS.axis}
            domain={['auto', 'auto']}
          />
          <Tooltip
            content={<PathTooltip decimals={decimals} />}
            cursor={{ stroke: COLORS.borderBright }}
          />
          <Area
            name="BAND"
            dataKey="band"
            stroke="none"
            fill={color}
            fillOpacity={0.14}
            isAnimationActive={false}
          />
          {fairValue != null && (
            <ReferenceLine
              y={fairValue}
              stroke={COLORS.textSecondary}
              strokeDasharray="5 4"
              label={{
                value: fairLabel ?? 'fair value',
                fill: COLORS.textSecondary,
                fontSize: 9,
                position: 'insideBottomRight',
              }}
            />
          )}
          <Line
            name="FORECAST"
            dataKey="FORECAST"
            stroke={color}
            strokeWidth={2.5}
            dot={{ r: 2.5, fill: color }}
            isAnimationActive={false}
            connectNulls
          />
        </ComposedChart>
      </ResponsiveContainer>
    </Panel>
  );
}

// ── integrated stance banner ──────────────────────────────────

const STANCE_META: Record<string, { label: string; color: string }> = {
  risk_off: { label: 'RISK-OFF', color: COLORS.negative },
  neutral: { label: 'NEUTRAL', color: COLORS.neutral },
  risk_on: { label: 'RISK-ON', color: COLORS.positive },
  unknown: { label: '—', color: COLORS.textDim },
};

const COMPONENT_COLOR: Record<string, string> = {
  cheap: COLORS.positive, improving: COLORS.positive, low: COLORS.positive, calm: COLORS.positive,
  fair: COLORS.neutral, stable: COLORS.neutral, normal: COLORS.neutral,
  rich: COLORS.negative, deteriorating: COLORS.negative, elevated: COLORS.accent,
  stressed: COLORS.negative, unknown: COLORS.textDim,
};

function CreditStanceBanner({ cv, meth }: {
  cv: NonNullable<MacroViews['credit_view']>;
  meth?: Methodology;
}) {
  const meta = STANCE_META[cv.stance] ?? STANCE_META.unknown;
  const m = meth ?? {};
  const e = cv.evidence ?? {};
  const fmt = (x: number | null | undefined, suf = '', d = 2) =>
    (x == null ? '—' : `${x.toFixed(d)}${suf}`);
  // Per-component: label, current value, the live evidence behind it, and the doc.
  const comps: { key: string; label: string; value: string; why: string; doc?: MethodologyEntry }[] = [
    {
      key: 'valuation', label: 'valuation', value: cv.components.valuation, doc: m.valuation,
      why: `HY OAS ${fmt(num(e.hy_oas_pct), '%')} · ${fmt(num(e.hy_oas_fairvalue_gap_pct) != null
        ? (e.hy_oas_fairvalue_gap_pct as number) * 100 : null, 'bp', 0)} vs macro fair value`,
    },
    {
      key: 'loss cycle', label: 'loss cycle', value: cv.components.loss_cycle, doc: m.loss_cycle,
      why: `loss rate ${fmt(num(e.default_rate_pct), '%')} · 4q Δ ${fmt(num(e.default_rate_4q_change_pct) != null
        ? (e.default_rate_4q_change_pct as number) * 100 : null, 'bp', 0)}`,
    },
    {
      key: 'volatility', label: 'volatility', value: cv.components.volatility, doc: m.volatility,
      why: `rates vol ${fmt(num(e.rates_vol_bp), 'bp', 0)} vs ${fmt(num(e.rates_vol_uncond_bp), 'bp', 0)} long-run`,
    },
    {
      key: 'funding', label: 'funding', value: cv.components.funding, doc: m.funding,
      why: `stress z ${fmt(num(e.funding_stress_z))} · CP–bill ${fmt(num(e.cp_bill_spread_bp), 'bp', 0)}`,
    },
  ];
  return (
    <Panel
      title="Credit Stance"
      subtitle={(
        <span>
          integrated read · {cv.n_signals} signals · score {cv.score ?? '—'}{' '}
          <DocLink m={m.credit_stance} />
        </span>
      )}
    >
      <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 16 }}>
        <div style={{ fontSize: 26, fontWeight: 700, color: meta.color, minWidth: 130 }}>
          {meta.label}
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, flex: 1 }}>
          {comps.map((c) => (
            <div key={c.key} style={{ border: `1px solid ${COLORS.border}`, padding: '4px 8px' }}>
              <span className="muted" style={{ fontSize: 9, textTransform: 'uppercase', letterSpacing: '0.1em' }}>
                {c.label}
              </span>
              <div style={{ fontSize: 12, fontWeight: 600, color: COMPONENT_COLOR[c.value] ?? COLORS.textPrimary }}>
                {c.value.replace(/_/g, ' ').toUpperCase()}
              </div>
            </div>
          ))}
        </div>
      </div>

      <details style={{ marginTop: 10 }}>
        <summary className="muted" style={{ fontSize: 10, letterSpacing: '0.1em',
          textTransform: 'uppercase', cursor: 'pointer' }}>
          Why this stance?
        </summary>
        <div style={{ marginTop: 6, display: 'flex', flexDirection: 'column', gap: 4 }}>
          {comps.map((c) => (
            <div key={c.key} style={{ display: 'flex', alignItems: 'baseline', gap: 8, fontSize: 11 }}>
              <span style={{ minWidth: 80, color: COMPONENT_COLOR[c.value] ?? COLORS.textPrimary }}>
                {c.value.replace(/_/g, ' ')}
              </span>
              <span className="muted" style={{ flex: 1 }}>{c.why}</span>
              <DocLink m={c.doc} />
            </div>
          ))}
        </div>
      </details>
      {(cv.scenarios?.length ?? 0) > 0 && (
        <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 10, marginTop: 10 }}>
          <span className="muted" style={{ fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase' }}>
            under stress
          </span>
          {cv.scenarios!.map((s) => {
            const m = STANCE_META[s.stance] ?? STANCE_META.unknown;
            return (
              <div key={s.name} title={s.rationale}
                style={{ border: `1px solid ${COLORS.border}`, padding: '3px 8px' }}>
                <span className="muted" style={{ fontSize: 9, textTransform: 'uppercase' }}>{s.name}</span>
                <span style={{ fontSize: 12, fontWeight: 600, color: m.color, marginLeft: 6 }}>
                  {m.label}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </Panel>
  );
}

// ── section ───────────────────────────────────────────────────

export default function CreditForecastsPanel({ views }: { views: MacroViews }) {
  const ev = views.regime?.evidence ?? {};
  const meth = views.methodology;
  const concepts = new Map((views.concepts ?? []).map((c) => [c.key, c]));

  const hyFair = num(ev.hy_oas_pct) != null && num(ev.hy_oas_fairvalue_gap_pct) != null
    ? (ev.hy_oas_pct as number) - (ev.hy_oas_fairvalue_gap_pct as number)
    : null;
  const igFair = num(ev.ig_oas_pct) != null && num(ev.ig_oas_fairvalue_gap_pct) != null
    ? (ev.ig_oas_pct as number) - (ev.ig_oas_fairvalue_gap_pct as number)
    : null;

  const hasAny = ['hy_oas', 'ig_oas', 'default_rate', 'rates_vol', 'hy_vol', 'funding_spread']
    .some((k) => concepts.has(k)) || num(ev.hy_oas_pct) != null;
  if (!hasAny) return null;

  return (
    <div className="stack">
      <div className="muted" style={{ fontSize: 10, letterSpacing: '0.14em', marginTop: 8 }}>
        CREDIT FORECASTS — SPREADS, DEFAULT CYCLE & VOLATILITY (the desk&apos;s P&amp;L variables)
      </div>

      {views.credit_view && views.credit_view.n_signals > 0 && (
        <CreditStanceBanner cv={views.credit_view} meth={meth} />
      )}

      <CreditTiles ev={ev} carry={views.credit_view?.carry_to_risk} meth={meth} />

      <div className="grid-2">
        <CreditPathChart
          concept={concepts.get('hy_oas')}
          color={COLORS.negative}
          decimals={2}
          fairValue={hyFair}
          fairLabel="macro fair value"
          unitLabel="% OAS · dashed = fair value · shaded = forecast band"
        />
        <CreditPathChart
          concept={concepts.get('ig_oas')}
          color={COLORS.chartSecondary}
          decimals={2}
          fairValue={igFair}
          fairLabel="macro fair value"
          unitLabel="% OAS · dashed = fair value · shaded = forecast band"
        />
        <CreditPathChart
          concept={concepts.get('default_rate')}
          color={COLORS.accent}
          decimals={2}
          unitLabel="% · charge-off / loss-rate cycle · shaded = forecast band"
        />
        <CreditPathChart
          concept={concepts.get('rates_vol')}
          color={COLORS.chart6mo}
          decimals={0}
          unitLabel="bp p.a. · GARCH conditional vol (MOVE proxy)"
        />
        <CreditPathChart
          concept={concepts.get('funding_spread')}
          color={COLORS.chartTertiary}
          decimals={0}
          unitLabel="bp · CP–bill funding spread · mean-reverting"
        />
      </div>

      <div className="grid-2">
        <FredSeriesPanel
          title="Credit Spread History"
          subtitle="current OAS, one point per run day"
          series={[
            { seriesId: 'MACRO_REGIME_HY_OAS_PCT', key: 'hy', name: 'HY OAS %', color: COLORS.negative },
            { seriesId: 'MACRO_REGIME_IG_OAS_PCT', key: 'ig', name: 'IG OAS %', color: COLORS.chartSecondary },
          ]}
          ranges={HISTORY_RANGES}
          defaultRange="MAX"
          decimals={2}
        />
        <FredSeriesPanel
          title="Valuation Gap History"
          subtitle="OAS − macro fair value · >0 cheap, <0 rich"
          series={[
            { seriesId: 'MACRO_REGIME_HY_OAS_FAIRVALUE_GAP_PCT', key: 'hy', name: 'HY GAP %', color: COLORS.negative },
            { seriesId: 'MACRO_REGIME_IG_OAS_FAIRVALUE_GAP_PCT', key: 'ig', name: 'IG GAP %', color: COLORS.chartSecondary },
          ]}
          ranges={HISTORY_RANGES}
          defaultRange="MAX"
          decimals={2}
          unit="plain"
        />
        <FredSeriesPanel
          title="Credit Stance History"
          subtitle="integrated stance score · >0 risk-off, <0 risk-on"
          series={[
            { seriesId: 'MACRO_CREDIT_STANCE_SCORE', key: 'score', name: 'STANCE SCORE', color: COLORS.accent },
          ]}
          ranges={HISTORY_RANGES}
          defaultRange="MAX"
          decimals={1}
          unit="plain"
        />
        <FredSeriesPanel
          title="Carry-to-Risk History"
          subtitle="OAS per unit of spread vol · higher = better paid"
          series={[
            { seriesId: 'MACRO_CREDIT_CARRY_HY', key: 'hy', name: 'HY CARRY', color: COLORS.negative },
            { seriesId: 'MACRO_CREDIT_CARRY_IG', key: 'ig', name: 'IG CARRY', color: COLORS.chartSecondary },
          ]}
          ranges={HISTORY_RANGES}
          defaultRange="MAX"
          decimals={2}
          unit="plain"
        />
      </div>
    </div>
  );
}
