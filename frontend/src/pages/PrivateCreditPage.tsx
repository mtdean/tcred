import BDCMonitorPanel from '../components/private_credit/BDCMonitorPanel';
import SectorPerformancePanel from '../components/private_credit/SectorPerformancePanel';

export default function PrivateCreditPage() {
  return (
    <div className="stack">
      <SectorPerformancePanel />
      <BDCMonitorPanel />
    </div>
  );
}
