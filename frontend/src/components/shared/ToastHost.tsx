// Renders the toast stack from lib/toast.ts. Mounted once in App.tsx.

import { useEffect, useState } from 'react';
import { dismissToast, subscribeToasts, type ToastMsg } from '../../lib/toast';

export default function ToastHost() {
  const [toasts, setToasts] = useState<ToastMsg[]>([]);

  useEffect(() => subscribeToasts(setToasts), []);

  if (toasts.length === 0) return null;
  return (
    <div className="toast-stack" role="status" aria-live="polite">
      {toasts.map((t) => (
        <button
          key={t.id}
          className={`toast mono ${t.kind === 'error' ? 'toast-error' : ''}`}
          onClick={() => dismissToast(t.id)}
          title="dismiss"
        >
          {t.text}
        </button>
      ))}
    </div>
  );
}
