// ABS / EDGAR page — sub-tab structure (Phase 7):
//   - SPREADS    AbsPricingPanel + SpreadTrackerPanel (424B5 + FWP tracker)
//   - TRUSTS     Master-trust monthly performance (10-D parser)
//   - EDGAR      Raw EDGAR filings feed
//   - KBRA       Presale parser (Phase 7)
//   - SIFMA      Aggregate issuance stats

import * as Tabs from '@radix-ui/react-tabs';

import EdgarFeed from '../components/abs/EdgarFeed';
import SifmaPanel from '../components/abs/SifmaPanel';
import AbsPricingPanel from '../components/abs/AbsPricingPanel';
import SpreadTrackerPanel from '../components/abs/SpreadTrackerPanel';
import KbraPresalesPanel from '../components/abs/KbraPresalesPanel';
import TrustPerformancePanel from '../components/abs/TrustPerformancePanel';
import { TraceVolumePanel } from '../components/macro/DashboardPanels';

export default function ABSPage() {
  return (
    <Tabs.Root defaultValue="spreads" className="stack">
      <Tabs.List className="subtabs" aria-label="ABS / EDGAR sections">
        <Tabs.Trigger value="spreads">Spreads</Tabs.Trigger>
        <Tabs.Trigger value="trusts">Trusts</Tabs.Trigger>
        <Tabs.Trigger value="edgar">EDGAR Feed</Tabs.Trigger>
        <Tabs.Trigger value="kbra">KBRA Presales</Tabs.Trigger>
        <Tabs.Trigger value="sifma">SIFMA</Tabs.Trigger>
      </Tabs.List>

      <Tabs.Content value="spreads" className="stack">
        <SpreadTrackerPanel />
        <AbsPricingPanel />
        <TraceVolumePanel />
      </Tabs.Content>

      <Tabs.Content value="trusts">
        <TrustPerformancePanel />
      </Tabs.Content>

      <Tabs.Content value="edgar">
        <EdgarFeed />
      </Tabs.Content>

      <Tabs.Content value="kbra">
        <KbraPresalesPanel />
      </Tabs.Content>

      <Tabs.Content value="sifma">
        <SifmaPanel />
      </Tabs.Content>
    </Tabs.Root>
  );
}
