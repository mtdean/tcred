// Relevance score as 5 filled/empty dots (●●●●○).

interface Props {
  score: number | null;
  size?: number;
}

export default function ScoreDots({ score, size = 11 }: Props) {
  const n = score ?? 0;
  return (
    <span title={`Relevance ${n}/5`} style={{ letterSpacing: 1, fontSize: size, whiteSpace: 'nowrap' }}>
      {[1, 2, 3, 4, 5].map((i) => (
        <span key={i} style={{ color: i <= n ? 'var(--text-accent)' : 'var(--border-bright)' }}>
          ●
        </span>
      ))}
    </span>
  );
}
