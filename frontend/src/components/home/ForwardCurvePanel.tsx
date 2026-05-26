// Treasury forward curve panel — today vs 6mo vs 1yr.

import { useQuery } from '@tanstack/react-query';
import { getForwardCurve } from '../../lib/api';
import { qk } from '../../lib/queryKeys';
import { fmtDate } from '../../lib/utils';
import Panel from '../shared/Panel';
import LoadingCursor from '../shared/LoadingCursor';
import EmptyState from '../shared/EmptyState';
import ForwardCurveChart from '../charts/ForwardCurveChart';

export default function ForwardCurvePanel() {
  const { data, isLoading, isError } = useQuery({
    queryKey: qk.forwardCurve,
    queryFn: () => getForwardCurve().then((r) => r.data),
  });

  const hasData = !!data && data.tenors.some((t) => data.today[t] != null);

  return (
    <Panel
      title="Treasury Forward Curve"
      subtitle={hasData && data?.as_of.today ? `SNAPSHOT ${fmtDate(data.as_of.today)}` : undefined}
    >
      {isLoading ? (
        <LoadingCursor />
      ) : isError || !hasData ? (
        <EmptyState message="NO FRED DATA — SET FRED_API_KEY AND REFRESH" />
      ) : (
        <ForwardCurveChart data={data} />
      )}
    </Panel>
  );
}
