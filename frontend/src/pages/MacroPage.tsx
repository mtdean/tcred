import DelinquencyPanel from '../components/macro/DelinquencyPanel';
import ChargeOffPanel from '../components/macro/ChargeOffPanel';
import RatesPanel from '../components/macro/RatesPanel';
import MacroIndicators from '../components/macro/MacroIndicators';
import H8CreditPanel from '../components/macro/H8CreditPanel';
import CloStressPanel from '../components/macro/CloStressPanel';
import {
  NationalActivityPanel,
  RecessionEnsemblePanel,
  SahmRulePanel,
  NearTermForwardSpreadPanel,
  JoblessClaimsPanel,
  LiquidityPanel,
  UsedVehicleValuesPanel,
  ConsumerComplaintsPanel,
  CreditImpulsePanel,
  ConsumerStressPanel,
  DelinquencyFlowPanel,
  OfrStressPanel,
  CreditGapPanel,
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
      <SectionLabel>Headline Indicators</SectionLabel>
      <MacroIndicators />

      <SectionLabel>Activity & Recession Risk</SectionLabel>
      <div className="grid-2">
        <RecessionEnsemblePanel />
        <ConsumerStressPanel />
        <SahmRulePanel />
        <NationalActivityPanel />
        <JoblessClaimsPanel />
      </div>

      <SectionLabel>Rates, Curve & Financial Conditions</SectionLabel>
      <div className="grid-2">
        <RatesPanel />
        <NearTermForwardSpreadPanel />
        <FinancialStressPanel />
        <OfrStressPanel />
        <LiquidityPanel />
      </div>

      <SectionLabel>Credit & Delinquency</SectionLabel>
      <div className="grid-2">
        <DelinquencyPanel />
        <ChargeOffPanel />
        <DelinquencyFlowPanel />
        <CreditImpulsePanel />
        <CreditGapPanel />
        <UsedVehicleValuesPanel />
        <ConsumerComplaintsPanel />
      </div>

      <SectionLabel>Bank Credit Supply (Fed H.8)</SectionLabel>
      <H8CreditPanel />

      <SectionLabel>CLO Stress</SectionLabel>
      <CloStressPanel />

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
    </div>
  );
}
