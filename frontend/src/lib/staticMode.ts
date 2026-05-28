// Helpers for static-snapshot builds (e.g. GitHub Pages). When VITE_STATIC_MODE
// is set, mutation endpoints are unavailable, so every refresh/generate button
// gets locked with a "not available" tooltip via these props.

import { STATIC_MODE_ENABLED } from './api';

export { STATIC_MODE_ENABLED };

type ButtonOverride = {
  disabled: true;
  onClick: (e: React.MouseEvent) => void;
  title: undefined;
  'data-static-disabled': true;
};

export function staticDisabledProps(): ButtonOverride | null {
  if (!STATIC_MODE_ENABLED) return null;
  return {
    disabled: true,
    onClick: (e) => e.preventDefault(),
    title: undefined,
    'data-static-disabled': true,
  };
}
