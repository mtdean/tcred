// Derive NBER recession [start, end] intervals from the USREC 0/1 monthly flag.
// USREC = 1 during recession months; we collapse runs of 1s into spans the
// MultiLineChart can shade with <ReferenceArea> bands.

import { useQuery } from '@tanstack/react-query';
import { getFredHistory } from '../../lib/api';
import { qk } from '../../lib/queryKeys';
import type { ShadedInterval } from './MultiLineChart';

export function useRecessionIntervals(limit = 1200): ShadedInterval[] {
  const { data } = useQuery({
    queryKey: qk.fredHistory('USREC'),
    queryFn: () => getFredHistory('USREC', limit).then((r) => r.data),
    staleTime: 24 * 60 * 60_000, // NBER dating barely changes
  });

  const pts = data ?? []; // ascending by date
  const intervals: ShadedInterval[] = [];
  let runStart: string | null = null;
  let prevDate: string | null = null;

  for (const p of pts) {
    const on = p.value != null && p.value >= 0.5;
    if (on && runStart == null) {
      runStart = p.date; // recession begins this month
    } else if (!on && runStart != null) {
      // recession ended last month; band runs to the prior observed month
      intervals.push({ start: runStart, end: prevDate ?? runStart });
      runStart = null;
    }
    prevDate = p.date;
  }
  // Still in recession at the end of the sample.
  if (runStart != null && prevDate != null) {
    intervals.push({ start: runStart, end: prevDate });
  }

  return intervals;
}
