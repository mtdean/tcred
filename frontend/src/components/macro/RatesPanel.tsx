// Rates: Fed Funds, 10Y, 2Y, and the 10Y-2Y spread (FRED, daily/monthly).

import { COLORS } from '../../lib/colors';
import FredSeriesPanel from './FredSeriesPanel';
import type { FredSeriesDef } from '../charts/useFredSeries';

const SERIES: FredSeriesDef[] = [
  { seriesId: 'FEDFUNDS', key: 'ff', name: 'FED FUNDS', color: COLORS.chartPrimary },
  { seriesId: 'DGS10', key: 'y10', name: '10Y', color: COLORS.chartSecondary },
  { seriesId: 'DGS2', key: 'y2', name: '2Y', color: COLORS.chartTertiary },
  { seriesId: 'T10Y2Y', key: 'spread', name: '10Y-2Y', color: COLORS.chart6mo },
];

const RANGES = [
  { label: '1Y', years: 1 },
  { label: '2Y', years: 2 },
  { label: '5Y', years: 5 },
  { label: 'MAX', years: null },
];

export default function RatesPanel() {
  return (
    <FredSeriesPanel
      title="Rates & Curve"
      series={SERIES}
      ranges={RANGES}
      defaultRange="2Y"
      limit={3000}
      decimals={2}
    />
  );
}
