import EdgarFeed from '../components/abs/EdgarFeed';
import SifmaPanel from '../components/abs/SifmaPanel';
import AbsPricingPanel from '../components/abs/AbsPricingPanel';

export default function ABSPage() {
  return (
    <div className="stack">
      <AbsPricingPanel />
      <EdgarFeed />
      <SifmaPanel />
    </div>
  );
}
