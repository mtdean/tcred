// Small ⓘ affordance that justifies a credit reading: hover shows the
// methodology (and paper cite); click opens the source notebook/paper.
// Kept deliberately tiny + muted so it never competes with the data.

import { COLORS } from '../../lib/colors';

export interface MethodologyEntry {
  label: string;
  explanation: string;
  paper: { cite: string; link: string } | null;
  notebook: string | null;
}

export default function DocLink({ m }: { m?: MethodologyEntry }) {
  if (!m) return null;
  const href = m.notebook ?? m.paper?.link;
  const title = m.paper ? `${m.explanation}\n\nSource: ${m.paper.cite}` : m.explanation;
  if (!href) {
    return (
      <span title={title} style={{ color: COLORS.textDim, fontSize: 10, cursor: 'help' }}>
        ⓘ
      </span>
    );
  }
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      title={title}
      onClick={(e) => e.stopPropagation()}
      style={{ color: COLORS.textDim, fontSize: 10, textDecoration: 'none', cursor: 'help' }}
      onMouseEnter={(e) => { e.currentTarget.style.color = COLORS.accent; }}
      onMouseLeave={(e) => { e.currentTarget.style.color = COLORS.textDim; }}
    >
      ⓘ
    </a>
  );
}
