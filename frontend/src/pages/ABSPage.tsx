import EdgarFeed from '../components/abs/EdgarFeed';
import SifmaPanel from '../components/abs/SifmaPanel';
import AbsPricingPanel from '../components/abs/AbsPricingPanel';
import SpreadTrackerPanel from '../components/abs/SpreadTrackerPanel';

export default function ABSPage() {
  return (
    <div className="stack">
      <AbsPricingPanel />
      <SpreadTrackerPanel />
      <EdgarFeed />
      <SifmaPanel />
    </div>
  );
}
