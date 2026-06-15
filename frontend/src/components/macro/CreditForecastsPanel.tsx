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

const HISTORY_RANGES = [
  { label: '3M', years: 0.25 },
  { label: '1Y', years: 1 },
  { label: 'MAX', years: null },
];

const num = (x: number | null | undefined): number | null =>
  x == null || Number.isNaN(x) ? null : x;

// ── valuation tiles ───────────────────────────────────────────

function Tile({ label, value, color, detail }: {
  label: string; value: string; color?: string; detail?: string;
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
      <div className="muted" style={{ fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase' }}>
        {label}
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

function CreditTiles({ ev }: { ev: Record<string, number | null> }) {
  const pct = (x: number | null, d = 2) => (x == null ? '—' : `${x.toFixed(d)}%`);
  const bp = (x: number | null) => (x == null ? '—' : `${Math.round(x)}bp`);

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
        detail={hyGap == null ? undefined
          : `${hyCall.word} · ${hyGap >= 0 ? '+' : ''}${Math.round(hyGap * 100)}bp vs fair`}
      />
      <Tile
        label="IG OAS"
        value={pct(num(ev.ig_oas_pct))}
        color={igCall.color}
        detail={igGap == null ? undefined
          : `${igCall.word} · ${igGap >= 0 ? '+' : ''}${Math.round(igGap * 100)}bp vs fair`}
      />
      <Tile
        label="Default / Loss Rate"
        value={pct(num(ev.default_rate_pct))}
        color={drChg == null ? undefined : drChg > 0 ? COLORS.negative : COLORS.positive}
        detail={num(ev.default_rate_4q_fcst_pct) == null ? undefined
          : `4q → ${pct(num(ev.default_rate_4q_fcst_pct))} (${drChg != null && drChg >= 0 ? '+' : ''}${drChg != null ? (drChg * 100).toFixed(0) : '—'}bp)`}
      />
      <Tile
        label="Rates Vol (MOVE-proxy)"
        value={bp(num(ev.rates_vol_bp))}
        detail={num(ev.rates_vol_persistence) == null ? undefined
          : `persistence ${num(ev.rates_vol_persistence)!.toFixed(2)}`}
      />
      <Tile
        label="HY Spread Vol"
        value={bp(num(ev.hy_vol_bp))}
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
            detail={num(ev.cp_bill_spread_bp) == null ? undefined
              : `CP–bill ${bp(num(ev.cp_bill_spread_bp))} · z ${z != null ? z.toFixed(2) : '—'}`}
          />
        );
      })()}
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

function CreditStanceBanner({ cv }: { cv: NonNullable<MacroViews['credit_view']> }) {
  const meta = STANCE_META[cv.stance] ?? STANCE_META.unknown;
  const comps: [string, string][] = [
    ['valuation', cv.components.valuation],
    ['loss cycle', cv.components.loss_cycle],
    ['volatility', cv.components.volatility],
    ['funding', cv.components.funding],
  ];
  return (
    <Panel title="Credit Stance" subtitle={`integrated read · ${cv.n_signals} signals · score ${cv.score ?? '—'}`}>
      <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 16 }}>
        <div style={{ fontSize: 26, fontWeight: 700, color: meta.color, minWidth: 130 }}>
          {meta.label}
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, flex: 1 }}>
          {comps.map(([k, v]) => (
            <div key={k} style={{ border: `1px solid ${COLORS.border}`, padding: '4px 8px' }}>
              <span className="muted" style={{ fontSize: 9, textTransform: 'uppercase', letterSpacing: '0.1em' }}>
                {k}
              </span>
              <div style={{ fontSize: 12, fontWeight: 600, color: COMPONENT_COLOR[v] ?? COLORS.textPrimary }}>
                {v.replace(/_/g, ' ').toUpperCase()}
              </div>
            </div>
          ))}
        </div>
      </div>
    </Panel>
  );
}

// ── section ───────────────────────────────────────────────────

export default function CreditForecastsPanel({ views }: { views: MacroViews }) {
  const ev = views.regime?.evidence ?? {};
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
        <CreditStanceBanner cv={views.credit_view} />
      )}

      <CreditTiles ev={ev} />

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
      </div>
    </div>
  );
}
