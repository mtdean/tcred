// Net charge-off rates (FRED, quarterly).

import { COLORS } from '../../lib/colors';
import FredSeriesPanel from './FredSeriesPanel';
import type { FredSeriesDef } from '../charts/useFredSeries';

const SERIES: FredSeriesDef[] = [
  { seriesId: 'CORCCACBS', key: 'card', name: 'CREDIT CARD', color: COLORS.chartPrimary },
  { seriesId: 'CORALACBS', key: 'all', name: 'ALL LOANS', color: COLORS.chartSecondary },
];

const RANGES = [
  { label: '2Y', years: 2 },
  { label: '5Y', years: 5 },
  { label: '10Y', years: 10 },
  { label: 'MAX', years: null },
];

export default function ChargeOffPanel() {
  return (
    <FredSeriesPanel
      title="Net Charge-Off Rates"
      series={SERIES}
      ranges={RANGES}
      defaultRange="5Y"
      limit={60}
    />
  );
}
