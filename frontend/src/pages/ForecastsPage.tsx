// Forecasts: the macrobot model-ensemble view as charts, not tables.
// Forward curve with model-disagreement band, per-concept fan charts showing
// every model's path against the weighted ensemble, and regime tiles.

import { useQuery } from '@tanstack/react-query';
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
import { getMacroViews } from '../lib/api';
import { COLORS } from '../lib/colors';
import type {
  MacroViewConcept,
  MacroViewForwardSegment,
  MacroViews,
} from '../lib/types';
import Panel from '../components/shared/Panel';
import LoadingCursor from '../components/shared/LoadingCursor';
import TooltipShell from '../components/charts/TooltipShell';
import FredSeriesPanel from '../components/macro/FredSeriesPanel';
import CreditForecastsPanel from '../components/macro/CreditForecastsPanel';
import DocLink, { type MethodologyEntry } from '../components/macro/DocLink';

// Stable colors / display names per model across all charts on the page.
const MODEL_META: Record<string, { name: string; color: string }> = {
  acm_term_structure: { name: 'ACM', color: COLORS.chartSecondary },
  diebold_li_dns: { name: 'DNS', color: COLORS.chartTertiary },
  ang_piazzesi_macro_finance: { name: 'ANG-PIAZZESI', color: COLORS.chart6mo },
  market_forwards: { name: 'MARKET', color: COLORS.textPrimary },
  smets_wouters_2007: { name: 'SMETS-WOUTERS', color: '#e67e22' },
  bvar_minnesota: { name: 'BVAR', color: '#f4978e' },
  stock_watson_dfm: { name: 'SW-DFM', color: '#5b8ff9' },
  mixed_frequency_dfm: { name: 'MF-DFM', color: COLORS.chartTertiary },
  excess_bond_premium: { name: 'EBP', color: COLORS.chartSecondary },
  yield_curve_recession_probit: { name: 'YC PROBIT', color: COLORS.chart6mo },
  nfci: { name: 'NFCI', color: '#f4978e' },
  sahm_rule: { name: 'SAHM', color: '#5b8ff9' },
  wu_xia_shadow_rate: { name: 'WU-XIA', color: '#9b59b6' },
};

const meta = (id: string) => MODEL_META[id] ?? { name: id.toUpperCase(), color: COLORS.textDim };

// Concepts worth a fan chart, grouped: rates first, then macro.
const FAN_CONCEPTS = [
  'ust_10y', 'ust_1y', 'tp_10y', 'policy_rate', 'gdp_growth', 'inflation',
];

function ChartTooltip({
  active, payload, label, decimals = 2,
}: {
  active?: boolean;
  payload?: { name?: string; value?: number | number[]; color?: string }[];
  label?: string | number;
  decimals?: number;
}) {
  if (!active || !payload?.length) return null;
  return (
    <TooltipShell title={String(label)}>
      {payload
        .filter((p) => p.name !== 'BAND' && p.value != null)
        .map((p) => (
          <div key={p.name} style={{ color: p.color, fontSize: 10 }}>
            {p.name}: {Array.isArray(p.value) ? '' : (p.value as number).toFixed(decimals)}
          </div>
        ))}
    </TooltipShell>
  );
}

// ── Forward curve ─────────────────────────────────────────────

function ForwardCurvePanel({ segments }: { segments: MacroViewForwardSegment[] }) {
  const modelIds = Array.from(
    new Set(segments.flatMap((s) => Object.keys(s.members))),
  );
  const rows = segments.map((s) => ({
    segment: s.segment,
    band: s.lo != null && s.hi != null ? [s.lo, s.hi] : undefined,
    ENSEMBLE: s.ensemble,
    ...Object.fromEntries(modelIds.map((m) => [meta(m).name, s.members[m]])),
  }));
  return (
    <Panel
      title="Implied Forward Curve"
      subtitle="from each model's fitted zero curve · shaded = ±1sd model disagreement"
    >
      <ResponsiveContainer width="100%" height={240}>
        <ComposedChart data={rows} margin={{ top: 6, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={COLORS.border} vertical={false} />
          <XAxis dataKey="segment" tick={{ fill: COLORS.axis, fontSize: 10 }} stroke={COLORS.axis} />
          <YAxis
            tick={{ fill: COLORS.axis, fontSize: 10 }}
            tickFormatter={(v) => v.toFixed(1)}
            width={40}
            stroke={COLORS.axis}
            domain={['auto', 'auto']}
          />
          <Tooltip content={<ChartTooltip />} cursor={{ stroke: COLORS.borderBright }} />
          <Area
            name="BAND"
            dataKey="band"
            stroke="none"
            fill={COLORS.accent}
            fillOpacity={0.14}
            isAnimationActive={false}
          />
          {modelIds.map((m) => (
            <Line
              key={m}
              name={meta(m).name}
              dataKey={meta(m).name}
              stroke={meta(m).color}
              strokeWidth={1}
              strokeDasharray="4 3"
              dot={{ r: 2.5, fill: meta(m).color }}
              isAnimationActive={false}
            />
          ))}
          <Line
            name="ENSEMBLE"
            dataKey="ENSEMBLE"
            stroke={COLORS.accent}
            strokeWidth={2.5}
            dot={{ r: 3.5, fill: COLORS.accent }}
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
      <div className="muted" style={{ fontSize: 10, marginTop: 4 }}>
        {modelIds.map((m) => (
          <span key={m} style={{ marginRight: 12 }}>
            <span style={{ color: meta(m).color }}>──</span> {meta(m).name}
          </span>
        ))}
        <span style={{ color: COLORS.accent }}>━━</span> ENSEMBLE
      </div>
    </Panel>
  );
}

// ── Concept fan charts ────────────────────────────────────────

function FanChart({ concept }: { concept: MacroViewConcept }) {
  const modelIds = Array.from(
    new Set(concept.path.flatMap((p) => Object.keys(p.members))),
  );
  const rows = concept.path.map((p) => ({
    h: p.h_m === 0 ? 'now' : `+${p.h_m}m`,
    band: p.lo != null && p.hi != null ? [p.lo, p.hi] : undefined,
    ENSEMBLE: p.ensemble,
    ...Object.fromEntries(modelIds.map((m) => [meta(m).name, p.members[m]])),
  }));
  // Latest weights (if backtested) for the subtitle.
  const lastWeights = concept.path.find((p) => Object.keys(p.weights).length > 0)?.weights;
  const weightsNote = lastWeights
    ? Object.entries(lastWeights)
        .map(([m, w]) => `${meta(m).name} ${(100 * (w ?? 0)).toFixed(0)}%`)
        .join(' · ')
    : null;
  const k = concept.band_multiplier;
  const bandNote = k != null && Math.abs(k - 1) > 0.01
    ? ` · band ×${k.toFixed(2)} (calibrated to 68%)`
    : '';
  const subtitle = (weightsNote
    ? `${concept.unit} · weights: ${weightsNote}`
    : concept.unit) + bandNote;
  return (
    <Panel
      title={concept.label}
      subtitle={subtitle}
    >
      <ResponsiveContainer width="100%" height={190}>
        <ComposedChart data={rows} margin={{ top: 6, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={COLORS.border} vertical={false} />
          <XAxis dataKey="h" tick={{ fill: COLORS.axis, fontSize: 10 }} stroke={COLORS.axis} />
          <YAxis
            tick={{ fill: COLORS.axis, fontSize: 10 }}
            tickFormatter={(v) => v.toFixed(1)}
            width={40}
            stroke={COLORS.axis}
            domain={['auto', 'auto']}
          />
          <Tooltip content={<ChartTooltip />} cursor={{ stroke: COLORS.borderBright }} />
          <Area
            name="BAND"
            dataKey="band"
            stroke="none"
            fill={COLORS.accent}
            fillOpacity={0.12}
            isAnimationActive={false}
          />
          {modelIds.map((m) => (
            <Line
              key={m}
              name={meta(m).name}
              dataKey={meta(m).name}
              stroke={meta(m).color}
              strokeWidth={1}
              strokeDasharray="3 3"
              dot={false}
              isAnimationActive={false}
              connectNulls
            />
          ))}
          <Line
            name="ENSEMBLE"
            dataKey="ENSEMBLE"
            stroke={COLORS.accent}
            strokeWidth={2.5}
            dot={false}
            isAnimationActive={false}
            connectNulls
          />
        </ComposedChart>
      </ResponsiveContainer>
    </Panel>
  );
}

// ── Regime tiles ──────────────────────────────────────────────

const REGIME_COLOR: Record<string, string> = {
  risk_on: COLORS.positive, low: COLORS.positive, easy: COLORS.positive,
  accommodative: COLORS.positive, accommodative_zlb: COLORS.positive,
  neutral: COLORS.neutral,
  tight: COLORS.accent, elevated: COLORS.accent,
  stressed: COLORS.negative, high: COLORS.negative, very_tight: COLORS.negative,
};

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
      {detail && (
        <div className="muted" style={{ fontSize: 10, marginTop: 2 }}>{detail}</div>
      )}
    </div>
  );
}

function RegimeTiles({ views }: { views: MacroViews }) {
  const labels = views.regime?.labels ?? {};
  const ev = views.regime?.evidence ?? {};
  const meth = views.methodology ?? {};
  const fmtLabel = (v: unknown) => String(v ?? '?').replace(/_/g, ' ').toUpperCase();
  const pct = (x: number | null | undefined, d = 1) => (x == null ? '—' : `${x.toFixed(d)}%`);
  const pRec = ev.p_recession_12m;
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
      <Tile
        label="Credit Regime"
        value={fmtLabel(labels.credit_regime)}
        color={REGIME_COLOR[String(labels.credit_regime)]}
        doc={meth.credit_regime}
        detail={ev.ebp_pp != null ? `EBP ${ev.ebp_pp.toFixed(2)}pp` : undefined}
      />
      <Tile
        label="Recession Risk (12m)"
        value={pRec == null ? fmtLabel(labels.recession_risk) : `${(100 * pRec).toFixed(0)}%`}
        color={REGIME_COLOR[String(labels.recession_risk)]}
        detail={`${fmtLabel(labels.recession_risk)} · src: ${fmtLabel(labels.recession_risk_source)}`}
      />
      <Tile
        label="GDP-at-Risk (4q, 5%)"
        value={pct(ev.gdp_at_risk_4q_q05_pct)}
        color={(ev.gdp_at_risk_4q_q05_pct ?? 0) < 0 ? COLORS.negative : COLORS.positive}
        detail={ev.gdp_at_risk_4q_es05_pct != null
          ? `expected shortfall ${pct(ev.gdp_at_risk_4q_es05_pct)}`
          : undefined}
      />
      <Tile
        label="Policy Stance"
        value={fmtLabel(labels.shadow_stance)}
        color={REGIME_COLOR[String(labels.shadow_stance)]}
        detail={ev.shadow_rate != null ? `shadow ${pct(ev.shadow_rate, 2)}` : undefined}
      />
      <Tile
        label="Curve"
        value={labels.curve_inverted ? 'INVERTED' : 'UPWARD'}
        color={labels.curve_inverted ? COLORS.negative : COLORS.positive}
        detail={ev.slope_10y_3m_pp != null ? `10y−3m ${ev.slope_10y_3m_pp.toFixed(2)}pp` : undefined}
      />
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────

export default function ForecastsPage() {
  const { data, isLoading } = useQuery({
    queryKey: ['macroViews'],
    queryFn: () => getMacroViews().then((r) => r.data),
    staleTime: 15 * 60_000,
  });

  if (isLoading) {
    return <div className="stack"><Panel title="Model Forecasts"><LoadingCursor /></Panel></div>;
  }
  if (!data?.available) {
    return (
      <div className="stack">
        <Panel title="Model Forecasts" subtitle="macrobot ensemble">
          <div className="muted" style={{ fontSize: 11 }}>
            No forecast bundle found ({data?.reason ?? 'unknown'}). Run macrobot&apos;s
            run_synthesis, then POST /api/macro/forecasts/refresh.
          </div>
        </Panel>
      </div>
    );
  }

  const concepts = new Map((data.concepts ?? []).map((c) => [c.key, c]));
  const fans = FAN_CONCEPTS.map((k) => concepts.get(k)).filter(
    (c): c is MacroViewConcept => c != null && c.path.length > 1,
  );

  return (
    <div className="stack">
      <div className="muted" style={{ fontSize: 10, letterSpacing: '0.1em' }}>
        MACROBOT MODEL ENSEMBLE · AS OF {data.asof?.toUpperCase()}
        {' '}· dashed = individual models · solid = inverse-MSE-weighted ensemble
      </div>

      <RegimeTiles views={data} />

      <CreditForecastsPanel views={data} />

      <div className="muted" style={{ fontSize: 10, letterSpacing: '0.14em', marginTop: 8 }}>
        RATES &amp; MACRO MODEL FORECASTS — IMPLIED CURVE AND PER-CONCEPT ENSEMBLE FANS
      </div>

      {(data.forward_curve?.length ?? 0) > 0 && (
        <ForwardCurvePanel segments={data.forward_curve!} />
      )}

      <div className="grid-2">
        {fans.map((c) => <FanChart key={c.key} concept={c} />)}
      </div>

      <div className="muted" style={{ fontSize: 10, letterSpacing: '0.14em', marginTop: 8 }}>
        VIEW HISTORY — HOW THE ENSEMBLE&apos;S CALLS HAVE EVOLVED (one point per run day)
      </div>
      <div className="grid-2">
        <FredSeriesPanel
          title="10Y View History"
          subtitle="ensemble fitted vs 12m-ahead"
          series={[
            { seriesId: 'MACRO_ENS_UST_10Y_0M', key: 'fit', name: 'FITTED 10Y', color: COLORS.chartSecondary },
            { seriesId: 'MACRO_ENS_UST_10Y_12M', key: 'fwd', name: '12M-AHEAD', color: COLORS.accent },
          ]}
          ranges={[{ label: '3M', years: 0.25 }, { label: '1Y', years: 1 }, { label: 'MAX', years: null }]}
          defaultRange="MAX"
          decimals={2}
        />
        <FredSeriesPanel
          title="Growth View History"
          subtitle="GDP nowcast vs GDP-at-Risk (5%)"
          series={[
            { seriesId: 'MACRO_ENS_GDP_GROWTH_0M', key: 'now', name: 'GDP NOWCAST %/q', color: COLORS.chartTertiary },
            { seriesId: 'MACRO_REGIME_GDP_AT_RISK_4Q_Q05_PCT', key: 'gar', name: 'GDP-AT-RISK 4Q %', color: COLORS.negative },
          ]}
          ranges={[{ label: '3M', years: 0.25 }, { label: '1Y', years: 1 }, { label: 'MAX', years: null }]}
          defaultRange="MAX"
          decimals={2}
        />
        <FredSeriesPanel
          title="Recession & Credit History"
          subtitle="ensemble probability and EBP"
          series={[
            { seriesId: 'MACRO_REGIME_P_RECESSION_12M', key: 'prec', name: 'P(REC 12M)', color: COLORS.negative },
            { seriesId: 'MACRO_REGIME_EBP_PP', key: 'ebp', name: 'EBP (pp)', color: COLORS.chartSecondary },
          ]}
          ranges={[{ label: '3M', years: 0.25 }, { label: '1Y', years: 1 }, { label: 'MAX', years: null }]}
          defaultRange="MAX"
          decimals={2}
          unit="plain"
        />
        <FredSeriesPanel
          title="Forward Segments History"
          subtitle="ensemble implied forwards"
          series={[
            { seriesId: 'MACRO_FWD_3M_12M', key: 's1', name: '3M→1Y', color: COLORS.chartTertiary },
            { seriesId: 'MACRO_FWD_12M_60M', key: 's2', name: '1Y→5Y', color: COLORS.chartSecondary },
            { seriesId: 'MACRO_FWD_60M_120M', key: 's3', name: '5Y→10Y', color: COLORS.accent },
          ]}
          ranges={[{ label: '3M', years: 0.25 }, { label: '1Y', years: 1 }, { label: 'MAX', years: null }]}
          defaultRange="MAX"
          decimals={2}
        />
      </div>
    </div>
  );
}
