// Panels adapted from the FRED "Favorite Economic Dashboard" (dashboard 9706).
// Each is a thin wrapper over the generic FredSeriesPanel.

import { COLORS } from '../../lib/colors';
import FredSeriesPanel from './FredSeriesPanel';
import type { FredSeriesDef } from '../charts/useFredSeries';
import { useRecessionIntervals } from '../charts/useRecessionIntervals';

const RANGES_MONTHLY = [
  { label: '2Y', years: 2 },
  { label: '5Y', years: 5 },
  { label: '10Y', years: 10 },
  { label: 'MAX', years: null },
];
const RANGES_QUARTERLY = [
  { label: '5Y', years: 5 },
  { label: '10Y', years: 10 },
  { label: 'MAX', years: null },
];
const RANGES_DAILY = [
  { label: '1Y', years: 1 },
  { label: '3Y', years: 3 },
  { label: '5Y', years: 5 },
  { label: '10Y', years: 10 },
  { label: 'MAX', years: null },
];

// ── Activity & recession ──────────────────────────────────────────
export function NationalActivityPanel() {
  const series: FredSeriesDef[] = [
    { seriesId: 'CFNAI', key: 'cfnai', name: 'CFNAI', color: COLORS.chartSecondary },
    { seriesId: 'CFNAIMA3', key: 'ma3', name: '3-MO AVG', color: COLORS.chartPrimary },
  ];
  return (
    <FredSeriesPanel
      title="National Activity (CFNAI)"
      subtitle="RECESSION RISK BELOW −0.7"
      series={series}
      ranges={RANGES_MONTHLY}
      defaultRange="5Y"
      unit="plain"
      decimals={2}
    />
  );
}

export function RecessionProbabilityPanel() {
  // Three 12-month recession probabilities on one axis (all %): the yield-curve
  // probit (NY Fed / Estrella-Mishkin), the Excess Bond Premium probit (the
  // academically strongest), and the smoothed Chauvet-Piger model for context.
  const series: FredSeriesDef[] = [
    { seriesId: 'EBP_REC_PROB', key: 'ebp', name: 'EBP PROBIT', color: COLORS.negative },
    { seriesId: 'NYFED_RECESSION_PROB', key: 'curve', name: 'YIELD-CURVE PROBIT', color: COLORS.chartSecondary },
    { seriesId: 'RECPROUSM156N', key: 'smoothed', name: 'SMOOTHED (CHAUVET-PIGER)', color: COLORS.chart12mo },
  ];
  return (
    <FredSeriesPanel
      title="Recession Probability (12-mo)"
      subtitle="P(RECESSION), %"
      series={series}
      ranges={RANGES_MONTHLY}
      defaultRange="10Y"
      unit="pct"
      decimals={1}
    />
  );
}

export function RecessionEnsemblePanel() {
  // Headline gauge: a meta-logit *stack* of three 12-mo recession-probability
  // models (performance-weighted), shown bold over its components plus the
  // equal-weight blend for comparison. Gray bands = NBER recessions (USREC).
  const series: FredSeriesDef[] = [
    { seriesId: 'RECESSION_RISK_ENSEMBLE', key: 'ens', name: 'ENSEMBLE (STACKED)', color: COLORS.negative },
    { seriesId: 'RECESSION_RISK_ENSEMBLE_EW', key: 'ew', name: 'EQUAL-WEIGHT', color: COLORS.neutral },
    { seriesId: 'NTFS_REC_PROB', key: 'ntfs', name: 'NTFS', color: COLORS.chartTertiary },
    { seriesId: 'NYFED_RECESSION_PROB', key: 'curve', name: 'YIELD CURVE', color: COLORS.chartSecondary },
    { seriesId: 'EBP_REC_PROB', key: 'ebp', name: 'EBP', color: COLORS.chartPrimary },
  ];
  const recessions = useRecessionIntervals();
  return (
    <FredSeriesPanel
      title="Recession Risk — Ensemble"
      subtitle="STACKED BLEND OF 3 MODELS, P(RECESSION 12-MO) %"
      series={series}
      ranges={RANGES_MONTHLY}
      defaultRange="10Y"
      unit="pct"
      decimals={1}
      shadedIntervals={recessions}
    />
  );
}

export function SahmRulePanel() {
  // Separate panel: the Sahm gap is in pp of unemployment, not a probability,
  // so it can't share the probit axis. Triggers at +0.50.
  const series: FredSeriesDef[] = [
    { seriesId: 'SAHMREALTIME', key: 'realtime', name: 'REAL-TIME', color: COLORS.chartPrimary },
    { seriesId: 'SAHMCURRENT', key: 'current', name: 'CURRENT (REVISED)', color: COLORS.chartSecondary },
  ];
  return (
    <FredSeriesPanel
      title="Sahm Rule"
      subtitle="TRIGGERS AT +0.50 pp"
      series={series}
      ranges={RANGES_MONTHLY}
      defaultRange="10Y"
      unit="plain"
      decimals={2}
    />
  );
}

export function NearTermForwardSpreadPanel() {
  // Engstrom-Sharpe: implied 3-mo yield 18 months out minus current 3-mo.
  const series: FredSeriesDef[] = [
    { seriesId: 'NEAR_TERM_FWD_SPREAD', key: 'ntfs', name: 'NTFS', color: COLORS.chartPrimary },
  ];
  return (
    <FredSeriesPanel
      title="Near-Term Forward Spread"
      subtitle="NEGATIVE = CUTS PRICED / RECESSION SIGNAL"
      series={series}
      ranges={RANGES_DAILY}
      defaultRange="5Y"
      unit="pct"
      decimals={2}
      limit={2700}
    />
  );
}

export function ConsumerStressPanel() {
  // Single-needle composite (z-score): Consumer DSR + SLOOS card tightening
  // (lagged 4Q) + flow into 30+ delinquency + real revolving-credit growth.
  const series: FredSeriesDef[] = [
    { seriesId: 'CFSI', key: 'cfsi', name: 'CFSI', color: COLORS.negative },
  ];
  return (
    <FredSeriesPanel
      title="Consumer Financial Stress (CFSI)"
      subtitle="σ FROM AVG; 0 = NORMAL, >0 = MORE STRESS"
      series={series}
      ranges={RANGES_QUARTERLY}
      defaultRange="10Y"
      unit="plain"
      decimals={2}
      limit={80}
    />
  );
}

export function DelinquencyFlowPanel() {
  // NY Fed transition rates: % of balances flowing into 30+ delinquency by loan
  // type. Flows lead the stock delinquency/charge-off series.
  const series: FredSeriesDef[] = [
    { seriesId: 'HHDC_FLOW30_CC', key: 'cc', name: 'CREDIT CARD', color: COLORS.negative },
    { seriesId: 'HHDC_FLOW30_AUTO', key: 'auto', name: 'AUTO', color: COLORS.chartPrimary },
    { seriesId: 'HHDC_FLOW30_STUDENT', key: 'student', name: 'STUDENT', color: COLORS.chart6mo },
    { seriesId: 'HHDC_FLOW30_ALL', key: 'all', name: 'ALL LOANS', color: COLORS.chartSecondary },
  ];
  return (
    <FredSeriesPanel
      title="Flow into Delinquency (30+, NY Fed)"
      subtitle="% OF BALANCES TRANSITIONING IN"
      series={series}
      ranges={RANGES_QUARTERLY}
      defaultRange="10Y"
      unit="pct"
      decimals={1}
      limit={100}
    />
  );
}

export function CreditImpulsePanel() {
  // Δ in the annual flow of credit (TCMDO) as % of GDP. Leads GDP ~9-12 months.
  const series: FredSeriesDef[] = [
    { seriesId: 'CREDIT_IMPULSE', key: 'impulse', name: 'CREDIT IMPULSE', color: COLORS.chartTertiary },
  ];
  return (
    <FredSeriesPanel
      title="Credit Impulse"
      subtitle="Δ CREDIT FLOW, % OF GDP"
      series={series}
      ranges={RANGES_QUARTERLY}
      defaultRange="10Y"
      unit="pct"
      decimals={1}
      limit={80}
    />
  );
}

// ── Financial conditions ──────────────────────────────────────────
export function FinancialStressPanel() {
  const series: FredSeriesDef[] = [
    { seriesId: 'STLFSI4', key: 'stl', name: 'ST LOUIS FSI', color: COLORS.chartPrimary },
    { seriesId: 'NFCI', key: 'nfci', name: 'CHICAGO NFCI', color: COLORS.chartSecondary },
  ];
  return (
    <FredSeriesPanel
      title="Financial Stress & Conditions"
      subtitle="0 = NORMAL; >0 = TIGHTER"
      series={series}
      ranges={RANGES_DAILY}
      defaultRange="5Y"
      unit="plain"
      decimals={2}
      limit={800}
    />
  );
}

export function OfrStressPanel() {
  // OFR FSI headline + its five additive category contributions (daily, global).
  const series: FredSeriesDef[] = [
    { seriesId: 'OFR_FSI', key: 'fsi', name: 'OFR FSI', color: COLORS.negative },
    { seriesId: 'OFR_FSI_CREDIT', key: 'credit', name: 'CREDIT', color: COLORS.chartPrimary },
    { seriesId: 'OFR_FSI_EQUITY', key: 'equity', name: 'EQUITY', color: COLORS.chartSecondary },
    { seriesId: 'OFR_FSI_SAFE', key: 'safe', name: 'SAFE ASSETS', color: COLORS.chartTertiary },
    { seriesId: 'OFR_FSI_FUNDING', key: 'funding', name: 'FUNDING', color: COLORS.chart6mo },
    { seriesId: 'OFR_FSI_VOL', key: 'vol', name: 'VOLATILITY', color: COLORS.neutral },
  ];
  return (
    <FredSeriesPanel
      title="OFR Financial Stress Index"
      subtitle="0 = AVG; >0 = STRESS (DAILY, GLOBAL)"
      series={series}
      ranges={RANGES_DAILY}
      defaultRange="5Y"
      unit="plain"
      decimals={2}
      limit={6800}
    />
  );
}

export function CreditGapPanel() {
  // BIS US private credit-to-GDP gap (pp). >+10pp = elevated banking-crisis risk.
  const series: FredSeriesDef[] = [
    { seriesId: 'BIS_CREDIT_GAP_US', key: 'gap', name: 'CREDIT-TO-GDP GAP', color: COLORS.chartPrimary },
  ];
  return (
    <FredSeriesPanel
      title="BIS Credit-to-GDP Gap (US)"
      subtitle="pp vs HP TREND; >+10 = ELEVATED CRISIS RISK"
      series={series}
      ranges={[
        { label: '10Y', years: 10 },
        { label: '20Y', years: 20 },
        { label: 'MAX', years: null },
      ]}
      defaultRange="20Y"
      unit="plain"
      decimals={1}
      limit={300}
    />
  );
}

// ── Lending standards ─────────────────────────────────────────────
export function LendingStandardsPanel() {
  const series: FredSeriesDef[] = [
    { seriesId: 'DRTSCILM', key: 'tighten', name: 'NET % TIGHTENING C&I', color: COLORS.chartPrimary },
  ];
  return (
    <FredSeriesPanel
      title="Bank Lending Standards (SLOOS)"
      subtitle="NET % OF BANKS TIGHTENING C&I"
      series={series}
      ranges={RANGES_QUARTERLY}
      defaultRange="10Y"
      unit="pct"
      decimals={0}
      limit={80}
    />
  );
}

// ── Inflation expectations ────────────────────────────────────────
export function InflationExpectationsPanel() {
  const series: FredSeriesDef[] = [
    { seriesId: 'T5YIE', key: 'be5', name: '5Y BREAKEVEN', color: COLORS.chartSecondary },
    { seriesId: 'T5YIFR', key: 'fwd', name: '5Y5Y FORWARD', color: COLORS.negative },
    { seriesId: 'MICH', key: 'mich', name: 'UMICH EXP', color: COLORS.chartPrimary },
    { seriesId: 'PCETRIM12M159SFRBDAL', key: 'trim', name: 'TRIMMED PCE', color: COLORS.chartTertiary },
  ];
  return (
    <FredSeriesPanel
      title="Inflation Expectations"
      subtitle="%, ANNUAL"
      series={series}
      ranges={RANGES_DAILY}
      defaultRange="5Y"
      unit="pct"
      decimals={1}
      limit={2700}
    />
  );
}

// ── Growth & markets ──────────────────────────────────────────────
export function GrowthTrackerPanel() {
  const series: FredSeriesDef[] = [
    { seriesId: 'GDPC1', key: 'gdp', name: 'REAL GDP', color: COLORS.chartPrimary, yoyPeriods: 4 },
    { seriesId: 'PCECC96', key: 'pce', name: 'REAL PCE', color: COLORS.chartSecondary, yoyPeriods: 4 },
    { seriesId: 'INDPRO', key: 'ip', name: 'IND. PRODUCTION', color: COLORS.chartTertiary, yoyPeriods: 12 },
    { seriesId: 'PAYEMS', key: 'pay', name: 'PAYROLLS', color: COLORS.chart6mo, yoyPeriods: 12 },
  ];
  return (
    <FredSeriesPanel
      title="Growth Tracker (YoY)"
      subtitle="YEAR-OVER-YEAR % CHANGE"
      series={series}
      ranges={RANGES_MONTHLY}
      defaultRange="5Y"
      unit="pct"
      decimals={1}
    />
  );
}

export function StockMomentumPanel() {
  const series: FredSeriesDef[] = [
    { seriesId: 'SP500', key: 'sp', name: 'S&P 500 YoY', color: COLORS.chartPrimary, yoyPeriods: 252 },
  ];
  return (
    <FredSeriesPanel
      title="Stock Market Momentum"
      subtitle="S&P 500, YoY %"
      series={series}
      ranges={[
        { label: '1Y', years: 1 },
        { label: '3Y', years: 3 },
        { label: '5Y', years: 5 },
        { label: 'MAX', years: null },
      ]}
      defaultRange="5Y"
      unit="pct"
      decimals={1}
      limit={1600}
    />
  );
}

export function DollarPanel() {
  const series: FredSeriesDef[] = [
    { seriesId: 'DTWEXBGS', key: 'usd', name: 'BROAD USD INDEX', color: COLORS.chartTertiary },
  ];
  return (
    <FredSeriesPanel
      title="U.S. Dollar Index"
      subtitle="NOMINAL BROAD, INDEX"
      series={series}
      ranges={RANGES_DAILY}
      defaultRange="5Y"
      unit="plain"
      decimals={1}
      limit={2700}
    />
  );
}
