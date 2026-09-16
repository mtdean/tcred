// Touch pull-to-refresh for the main scroll container (iPad/iPhone). Dragging
// down while scrolled to the top reveals an indicator; releasing past the
// threshold fires the same route-aware refresh as the TopBar button
// (useTabRefresh), so each tab pulls its own upstream sources. Renders the
// <main class="app-main"> element itself so it owns the scroll position.
//
// Touch listeners are attached natively (not via React props) because React
// registers touchmove as passive on the root, and we need preventDefault to
// stop the native scroll while the indicator is out.

import { useEffect, useRef, useState, type ReactNode } from 'react';
import { RefreshCw } from 'lucide-react';
import { STATIC_MODE_ENABLED } from '../../lib/staticMode';
import { useTabRefresh } from '../../lib/useTabRefresh';

const THRESHOLD = 60; // damped px that arms a refresh on release
const MAX_PULL = 90;
const damp = (dy: number) => Math.min(MAX_PULL, dy * 0.4);

export default function PullToRefresh({ children }: { children: ReactNode }) {
  const { spec, isPending, refresh } = useTabRefresh();
  const elRef = useRef<HTMLElement | null>(null);
  const [pull, setPull] = useState(0);
  const [dragging, setDragging] = useState(false);

  // Native listeners are registered once; they read the latest render state
  // through this ref.
  const live = useRef({ enabled: false, refresh });
  live.current = {
    enabled: !!spec && !isPending && !STATIC_MODE_ENABLED,
    refresh,
  };

  useEffect(() => {
    const el = elRef.current;
    if (!el) return;
    let startY = 0;
    let tracking = false;
    let dy = 0;

    const reset = () => {
      tracking = false;
      dy = 0;
      setDragging(false);
      setPull(0);
    };
    const onStart = (e: TouchEvent) => {
      if (!live.current.enabled || el.scrollTop > 0) return;
      startY = e.touches[0].clientY;
      tracking = true;
      dy = 0;
    };
    const onMove = (e: TouchEvent) => {
      if (!tracking) return;
      dy = e.touches[0].clientY - startY;
      if (dy <= 0 || el.scrollTop > 0) {
        // Finger went back up (or content scrolled): hand control to native
        // scrolling for the rest of this gesture.
        reset();
        return;
      }
      e.preventDefault();
      setDragging(true);
      setPull(damp(dy));
    };
    const onEnd = () => {
      if (!tracking) return;
      const armed = damp(dy) >= THRESHOLD;
      reset();
      if (armed && live.current.enabled) live.current.refresh();
    };

    el.addEventListener('touchstart', onStart, { passive: true });
    el.addEventListener('touchmove', onMove, { passive: false });
    el.addEventListener('touchend', onEnd);
    el.addEventListener('touchcancel', reset);
    return () => {
      el.removeEventListener('touchstart', onStart);
      el.removeEventListener('touchmove', onMove);
      el.removeEventListener('touchend', onEnd);
      el.removeEventListener('touchcancel', reset);
    };
  }, []);

  const height = isPending ? 40 : pull;
  const armed = pull >= THRESHOLD;

  return (
    <main className="app-main" ref={elRef}>
      <div
        className="ptr-indicator"
        style={{
          height,
          transition: dragging ? 'none' : 'height 0.2s ease',
        }}
        aria-hidden={height === 0}
      >
        {height > 0 && (
          <>
            <RefreshCw
              size={14}
              style={
                isPending
                  ? { animation: 'spin 1s linear infinite' }
                  : { transform: `rotate(${(pull / THRESHOLD) * 180}deg)` }
              }
            />
            {isPending
              ? 'REFRESHING'
              : armed
                ? `RELEASE FOR ${spec?.shortLabel ?? ''}`
                : 'PULL TO REFRESH'}
          </>
        )}
      </div>
      {children}
    </main>
  );
}
