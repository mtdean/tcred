import DelinquencyPanel from '../components/macro/DelinquencyPanel';
import ChargeOffPanel from '../components/macro/ChargeOffPanel';
import RatesPanel from '../components/macro/RatesPanel';
import MacroIndicators from '../components/macro/MacroIndicators';
import H8CreditPanel from '../components/macro/H8CreditPanel';
import CloStressPanel from '../components/macro/CloStressPanel';
import PluginSourcePanels from '../components/macro/PluginSourcePanels';
import TrustPerformancePanel from '../components/abs/TrustPerformancePanel';
import ConsumerScorecardPanel, {
  LeveragedScorecardPanel,
  SmbScorecardPanel,
} from '../components/macro/ConsumerScorecardPanel';
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
  DecompressionPanel,
  ConsumerCashflowPanel,
  LaborSlackPanel,
  ConsumerExpectationsPanel,
  SbaLendingPanel,
  AutoLoanRatesPanel,
  AutoLoanMaturityPanel,
  LoanDemandPanel,
  InterestCoveragePanel,
  CardPaymentBehaviorPanel,
  SmallBizLoansBySizePanel,
  CiQualityBySizePanel,
  SmbLoanRatesPanel,
  SmbLendingConditionsPanel,
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
        <LaborSlackPanel />
      </div>

      <SectionLabel>Rates, Curve & Financial Conditions</SectionLabel>
      <div className="grid-2">
        <RatesPanel />
        <NearTermForwardSpreadPanel />
        <FinancialStressPanel />
        <OfrStressPanel />
        <LiquidityPanel />
      </div>

      <SectionLabel>Consumer Health</SectionLabel>
      <ConsumerScorecardPanel />
      <ConsumerExpectationsPanel />
      <CardPaymentBehaviorPanel />

      <SectionLabel>Business & SMB Credit</SectionLabel>
      <SmbScorecardPanel />
      <SbaLendingPanel />
      <div className="grid-2">
        <SmallBizLoansBySizePanel />
        <CiQualityBySizePanel />
        <SmbLoanRatesPanel />
        <SmbLendingConditionsPanel />
      </div>

      <SectionLabel>Credit & Delinquency</SectionLabel>
      <div className="grid-2">
        <DelinquencyPanel />
        <ChargeOffPanel />
        <DelinquencyFlowPanel />
        <CreditImpulsePanel />
        <CreditGapPanel />
        <UsedVehicleValuesPanel />
        <AutoLoanRatesPanel />
        <AutoLoanMaturityPanel />
        <ConsumerComplaintsPanel />
        <ConsumerCashflowPanel />
      </div>

      {/* Monthly card master-trust actuals from 10-Ds — the high-frequency
          complement to the quarterly FRED bank data above. (Rescued from the
          parked ABS tab.) */}
      <SectionLabel>Card Trust Performance (10-D Monthly)</SectionLabel>
      <TrustPerformancePanel />

      <SectionLabel>Bank Credit Supply (Fed H.8)</SectionLabel>
      <H8CreditPanel />

      <SectionLabel>CLO & Leveraged Credit Stress</SectionLabel>
      <LeveragedScorecardPanel />
      <div className="grid-2">
        <CloStressPanel />
        <DecompressionPanel />
        <InterestCoveragePanel />
      </div>

      <SectionLabel>Lending Standards & Demand</SectionLabel>
      <div className="grid-2">
        <LendingStandardsPanel />
        <LoanDemandPanel />
      </div>

      <SectionLabel>Growth & Markets</SectionLabel>
      <div className="grid-2">
        <GrowthTrackerPanel />
        <StockMomentumPanel />
        <DollarPanel />
        <InflationExpectationsPanel />
      </div>

      {/* Data-source plugins (plugins/*.py) that declare a panel. */}
      <PluginSourcePanels header={<SectionLabel>Custom Sources</SectionLabel>} />
    </div>
  );
}
