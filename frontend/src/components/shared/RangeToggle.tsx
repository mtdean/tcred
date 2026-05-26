// Small inline range selector, e.g. [1M] [3M] [1Y]. Bloomberg-style button group.

interface Props<T extends string> {
  options: readonly T[];
  value: T;
  onChange: (v: T) => void;
}

export default function RangeToggle<T extends string>({ options, value, onChange }: Props<T>) {
  return (
    <div style={{ display: 'inline-flex', gap: 2 }}>
      {options.map((opt) => {
        const active = opt === value;
        return (
          <button
            key={opt}
            className="btn"
            onClick={() => onChange(opt)}
            style={{
              padding: '2px 6px',
              fontSize: 10,
              color: active ? 'var(--text-accent)' : 'var(--text-secondary)',
              borderColor: active ? 'var(--text-accent)' : 'var(--border-bright)',
            }}
          >
            {opt}
          </button>
        );
      })}
    </div>
  );
}
