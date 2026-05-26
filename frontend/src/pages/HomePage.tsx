import DigestPanel from '../components/home/DigestPanel';
import HYOASPanel from '../components/home/HYOASPanel';
import ForwardCurvePanel from '../components/home/ForwardCurvePanel';
import SofrPanel from '../components/home/SofrPanel';
import TopArticlesPanel from '../components/home/TopArticlesPanel';
import RecentFilingsStrip from '../components/home/RecentFilingsStrip';

export default function HomePage() {
  return (
    <div className="stack">
      <div className="grid-2">
        <DigestPanel />
        <HYOASPanel />
      </div>

      <div className="grid-2">
        <ForwardCurvePanel />
        <SofrPanel />
      </div>

      <div className="grid-2">
        <TopArticlesPanel title="Top Stories — Credit" category="credit" />
        <TopArticlesPanel title="Top Stories — Macro" category="macro" />
      </div>

      <RecentFilingsStrip />
    </div>
  );
}
