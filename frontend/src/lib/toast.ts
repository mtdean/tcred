// Tiny pub/sub toast bus — no context/provider ceremony. Callers fire
// showToast(); the single <ToastHost> mounted in App.tsx renders the stack.

export interface ToastMsg {
  id: number;
  text: string;
  kind: 'success' | 'error';
}

type Listener = (toasts: ToastMsg[]) => void;

let toasts: ToastMsg[] = [];
let nextId = 1;
const listeners = new Set<Listener>();

function emit() {
  for (const l of listeners) l(toasts);
}

export function showToast(text: string, kind: ToastMsg['kind'] = 'success', ttlMs = 5000) {
  const id = nextId++;
  toasts = [...toasts, { id, text, kind }];
  emit();
  setTimeout(() => dismissToast(id), ttlMs);
}

export function dismissToast(id: number) {
  toasts = toasts.filter((t) => t.id !== id);
  emit();
}

export function subscribeToasts(fn: Listener): () => void {
  listeners.add(fn);
  fn(toasts);
  return () => {
    listeners.delete(fn);
  };
}
