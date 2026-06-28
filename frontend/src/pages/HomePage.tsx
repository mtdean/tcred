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
        {/* Stacked rates column matches the digest's height — three equal
            grid rows so HY OAS no longer leaves dead space below it. */}
        <div
          style={{
            display: 'grid',
            gridTemplateRows: '1fr 1fr 1fr',
            gap: 12,
            minHeight: 0,
          }}
        >
          <HYOASPanel />
          <ForwardCurvePanel />
          <SofrPanel />
        </div>
      </div>

      <div className="grid-2">
        <TopArticlesPanel title="Top Stories — Credit" category="credit" />
        <TopArticlesPanel title="Top Stories — Macro" category="macro" />
      </div>

      <div className="grid-2">
        <TopArticlesPanel
          title="Research & Analyst Notes"
          sourceType="research"
          minScore={3}
          limit={6}
        />
        <TopArticlesPanel
          title="Distressed & Restructuring"
          sourceType="restructuring"
          minScore={3}
          limit={6}
        />
      </div>

      <RecentFilingsStrip />
    </div>
  );
}
