// Colored category tag. Maps a feed_category to its design-system color.

const CATEGORY_COLOR: Record<string, string> = {
  macro: 'var(--cat-macro)',
  credit: 'var(--cat-credit)',
  abs: 'var(--cat-abs)',
  fintech: 'var(--cat-fintech)',
  data: 'var(--cat-data)',
};

interface Props {
  category: string;
}

export default function CategoryChip({ category }: Props) {
  const key = category?.toLowerCase() ?? '';
  const color = CATEGORY_COLOR[key] ?? 'var(--text-secondary)';
  return (
    <span className="cat-chip" style={{ color }}>
      {category}
    </span>
  );
}
