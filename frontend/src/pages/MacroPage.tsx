import { Fragment } from 'react';
import { useSearchParams } from 'react-router-dom';
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

// Broad categories for the filter bar. Each section below belongs to one;
// "All" shows every section in this order. Kept in the URL (?cat=) so a view
// can be bookmarked or shared.
const CATEGORIES = [
  { id: 'all', label: 'All' },
  { id: 'growth', label: 'Recession & Growth' },
  { id: 'rates', label: 'Rates & Conditions' },
  { id: 'consumer', label: 'Consumer' },
  { id: 'smb', label: 'Business & SMB' },
  { id: 'corporate', label: 'Corporate & Leveraged' },
  { id: 'banks', label: 'Banks & Credit Supply' },
] as const;
type CategoryId = (typeof CATEGORIES)[number]['id'];

interface Section {
  cat: Exclude<CategoryId, 'all'>;
  label: string;
  body: React.ReactNode;
}

const SECTIONS: Section[] = [
  {
    cat: 'growth',
    label: 'Activity & Recession Risk',
    body: (
      <div className="grid-2">
        <RecessionEnsemblePanel />
        <SahmRulePanel />
        <NationalActivityPanel />
        <JoblessClaimsPanel />
        <LaborSlackPanel />
        <GrowthTrackerPanel />
        <StockMomentumPanel />
        <DollarPanel />
      </div>
    ),
  },
  {
    cat: 'rates',
    label: 'Rates, Curve & Financial Conditions',
    body: (
      <div className="grid-2">
        <RatesPanel />
        <NearTermForwardSpreadPanel />
        <FinancialStressPanel />
        <OfrStressPanel />
        <LiquidityPanel />
        <InflationExpectationsPanel />
      </div>
    ),
  },
  {
    cat: 'consumer',
    label: 'Consumer Health',
    body: (
      <>
        <ConsumerScorecardPanel />
        <ConsumerExpectationsPanel />
        <div className="grid-2">
          <ConsumerStressPanel />
          <CardPaymentBehaviorPanel />
          <DelinquencyPanel />
          <ChargeOffPanel />
          <DelinquencyFlowPanel />
          <ConsumerCashflowPanel />
        </div>
      </>
    ),
  },
  {
    cat: 'consumer',
    label: 'Auto Credit',
    body: (
      <div className="grid-2">
        <UsedVehicleValuesPanel />
        <AutoLoanRatesPanel />
        <AutoLoanMaturityPanel />
        <ConsumerComplaintsPanel />
      </div>
    ),
  },
  {
    // Monthly card master-trust actuals from 10-Ds — the high-frequency
    // complement to the quarterly bank data above.
    cat: 'consumer',
    label: 'Card Trust Performance (10-D Monthly)',
    body: <TrustPerformancePanel />,
  },
  {
    cat: 'smb',
    label: 'Business & SMB Credit',
    body: (
      <>
        <SmbScorecardPanel />
        <SbaLendingPanel />
        <div className="grid-2">
          <SmallBizLoansBySizePanel />
          <CiQualityBySizePanel />
          <SmbLoanRatesPanel />
          <SmbLendingConditionsPanel />
        </div>
      </>
    ),
  },
  {
    cat: 'corporate',
    label: 'CLO & Leveraged Credit Stress',
    body: (
      <>
        <LeveragedScorecardPanel />
        <div className="grid-2">
          <CloStressPanel />
          <DecompressionPanel />
          <InterestCoveragePanel />
        </div>
      </>
    ),
  },
  {
    cat: 'banks',
    label: 'Bank Credit Supply (Fed H.8)',
    body: <H8CreditPanel />,
  },
  {
    cat: 'banks',
    label: 'Lending Standards, Demand & Credit Cycle',
    body: (
      <div className="grid-2">
        <LendingStandardsPanel />
        <LoanDemandPanel />
        <CreditImpulsePanel />
        <CreditGapPanel />
      </div>
    ),
  },
];

function CategoryBar({ value, onChange }: { value: CategoryId; onChange: (v: CategoryId) => void }) {
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
      {CATEGORIES.map((c) => {
        const active = c.id === value;
        return (
          <button
            key={c.id}
            className="btn"
            onClick={() => onChange(c.id)}
            aria-pressed={active}
            style={{
              padding: '4px 10px',
              fontSize: 11,
              letterSpacing: '0.06em',
              textTransform: 'uppercase',
              color: active ? 'var(--text-accent)' : 'var(--text-secondary)',
              borderColor: active ? 'var(--text-accent)' : 'var(--border-bright)',
            }}
          >
            {c.label}
          </button>
        );
      })}
    </div>
  );
}

export default function MacroPage() {
  const [params, setParams] = useSearchParams();
  const raw = params.get('cat');
  const cat: CategoryId = CATEGORIES.some((c) => c.id === raw) ? (raw as CategoryId) : 'all';
  const setCat = (next: CategoryId) =>
    setParams(next === 'all' ? {} : { cat: next }, { replace: true });

  const sections = cat === 'all' ? SECTIONS : SECTIONS.filter((s) => s.cat === cat);

  return (
    <div className="stack">
      <CategoryBar value={cat} onChange={setCat} />

      {cat === 'all' && (
        <>
          <SectionLabel>Headline Indicators</SectionLabel>
          <MacroIndicators />
        </>
      )}

      {sections.map((s) => (
        <Fragment key={s.label}>
          <SectionLabel>{s.label}</SectionLabel>
          {s.body}
        </Fragment>
      ))}

      {/* Data-source plugins (plugins/*.py) that declare a panel. */}
      {cat === 'all' && (
        <PluginSourcePanels header={<SectionLabel>Custom Sources</SectionLabel>} />
      )}
    </div>
  );
}
