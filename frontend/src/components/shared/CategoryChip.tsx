// Colored category tag. Maps a feed_category to its design-system color.
// Both the short legacy keys and the long yaml-taxonomy names resolve to
// the same hue so the chip always renders branded.

export const CATEGORY_COLOR: Record<string, string> = {
  macro: 'var(--cat-macro)',
  credit: 'var(--cat-credit)',
  abs: 'var(--cat-abs)',
  structured_finance: 'var(--cat-abs)',
  fintech: 'var(--cat-fintech)',
  data: 'var(--cat-data)',
  data_science: 'var(--cat-data)',
};

export function categoryColor(category: string): string {
  return CATEGORY_COLOR[(category ?? '').toLowerCase()] ?? 'var(--text-secondary)';
}

interface Props {
  category: string;
}

export default function CategoryChip({ category }: Props) {
  return (
    <span className="cat-chip" style={{ color: categoryColor(category) }}>
      {category}
    </span>
  );
}
