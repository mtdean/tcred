// Delinquency rates by product (FRED, quarterly).

import { COLORS } from '../../lib/colors';
import FredSeriesPanel from './FredSeriesPanel';
import type { FredSeriesDef } from '../charts/useFredSeries';

const SERIES: FredSeriesDef[] = [
  { seriesId: 'DRCCLACBS', key: 'card', name: 'CREDIT CARD', color: COLORS.chartPrimary },
  { seriesId: 'DRSFRMACBS', key: 'mortgage', name: 'MORTGAGE', color: COLORS.chartSecondary },
  { seriesId: 'DRCLACBS', key: 'consumer', name: 'CONSUMER', color: COLORS.chartTertiary },
  { seriesId: 'DRBLACBS', key: 'business', name: 'BUSINESS', color: COLORS.chart6mo },
  { seriesId: 'DRCRELEXFACBS', key: 'cre', name: 'CRE', color: COLORS.negative },
];

const RANGES = [
  { label: '2Y', years: 2 },
  { label: '5Y', years: 5 },
  { label: '10Y', years: 10 },
  { label: 'MAX', years: null },
];

export default function DelinquencyPanel() {
  return (
    <FredSeriesPanel
      title="Delinquency Rates by Product"
      series={SERIES}
      ranges={RANGES}
      defaultRange="5Y"
      limit={60}
    />
  );
}
