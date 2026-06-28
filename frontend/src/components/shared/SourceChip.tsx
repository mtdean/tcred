// Prominent provenance tag: the publication/feed an article came from,
// color-coded by source type (blue = news/RSS, purple = newsletter).

import { Mail, Newspaper } from 'lucide-react';
import type { SourceType } from '../../lib/types';
import { sourceColor } from '../../lib/colors';

interface Props {
  feedName: string;
  sourceType: SourceType;
}

const NEWSLETTER_TYPES: ReadonlySet<SourceType> = new Set([
  'letter',
  'research',
  'restructuring',
]);

export default function SourceChip({ feedName, sourceType }: Props) {
  const isLetter = NEWSLETTER_TYPES.has(sourceType);
  const color = sourceColor(feedName);
  const Icon = isLetter ? Mail : Newspaper;
  return (
    <span
      className="cat-chip"
      title={`${isLetter ? 'Newsletter' : 'RSS news'}: ${feedName}`}
      style={{
        color,
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        maxWidth: 220,
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
      }}
    >
      <Icon size={9} style={{ flexShrink: 0 }} />
      {feedName}
    </span>
  );
}
