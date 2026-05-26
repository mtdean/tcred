import DelinquencyPanel from '../components/macro/DelinquencyPanel';
import ChargeOffPanel from '../components/macro/ChargeOffPanel';
import RatesPanel from '../components/macro/RatesPanel';
import MacroIndicators from '../components/macro/MacroIndicators';
import {
  NationalActivityPanel,
  RecessionProbabilityPanel,
  FinancialStressPanel,
  LendingStandardsPanel,
  InflationExpectationsPanel,
  GrowthTrackerPanel,
  StockMomentumPanel,
  DollarPanel,
} from '../components/macro/DashboardPanels';

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="muted"
      style={{
        fontSize: 10,
        letterSpacing: '0.14em',
        textTransform: 'uppercase',
        borderBottom: '1px solid var(--border)',
        paddingBottom: 4,
        marginTop: 4,
      }}
    >
      {children}
    </div>
  );
}

export default function MacroPage() {
  return (
    <div className="stack">
      <SectionLabel>Activity & Recession Risk</SectionLabel>
      <div className="grid-2">
        <NationalActivityPanel />
        <RecessionProbabilityPanel />
      </div>

      <SectionLabel>Rates, Curve & Financial Conditions</SectionLabel>
      <div className="grid-2">
        <RatesPanel />
        <FinancialStressPanel />
      </div>

      <SectionLabel>Credit & Delinquency</SectionLabel>
      <div className="grid-2">
        <DelinquencyPanel />
        <ChargeOffPanel />
      </div>

      <SectionLabel>Lending Standards & Inflation</SectionLabel>
      <div className="grid-2">
        <LendingStandardsPanel />
        <InflationExpectationsPanel />
      </div>

      <SectionLabel>Growth & Markets</SectionLabel>
      <div className="grid-2">
        <GrowthTrackerPanel />
        <StockMomentumPanel />
        <DollarPanel />
      </div>

      <SectionLabel>Headline Indicators</SectionLabel>
      <MacroIndicators />
    </div>
  );
}
