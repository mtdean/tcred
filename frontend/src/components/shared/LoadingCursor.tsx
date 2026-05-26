// Blinking-cursor loading indicator — terminal style, no spinners.

interface Props {
  label?: string;
}

export default function LoadingCursor({ label = 'LOADING' }: Props) {
  return (
    <span className="loading-cursor muted" style={{ letterSpacing: '0.08em' }}>
      {label}
    </span>
  );
}
