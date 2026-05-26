// Panels adapted from the FRED "Favorite Economic Dashboard" (dashboard 9706).
// Each is a thin wrapper over the generic FredSeriesPanel.

import { COLORS } from '../../lib/colors';
import FredSeriesPanel from './FredSeriesPanel';
import type { FredSeriesDef } from '../charts/useFredSeries';

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
  const series: FredSeriesDef[] = [
    { seriesId: 'RECPROUSM156N', key: 'prob', name: 'RECESSION PROB', color: COLORS.negative },
  ];
  return (
    <FredSeriesPanel
      title="Recession Probability"
      subtitle="SMOOTHED, %"
      series={series}
      ranges={RANGES_MONTHLY}
      defaultRange="10Y"
      unit="pct"
      decimals={1}
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
