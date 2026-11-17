import BDCMonitorPanel from '../components/private_credit/BDCMonitorPanel';
import FloatingRatePanel from '../components/private_credit/FloatingRatePanel';
import SectorPerformancePanel from '../components/private_credit/SectorPerformancePanel';

export default function PrivateCreditPage() {
  return (
    <div className="stack">
      <FloatingRatePanel />
      <SectorPerformancePanel />
      <BDCMonitorPanel />
    </div>
  );
}
