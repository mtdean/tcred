// Literal hex mirrors of the CSS palette, for use in SVG/recharts where
// CSS custom properties don't resolve as presentation attributes.

export const COLORS = {
  bgPanel: '#111111',
  border: '#2a2a2a',
  borderBright: '#3a3a3a',
  textPrimary: '#e8e8e8',
  textSecondary: '#8a8a8a',
  textDim: '#555555',
  axis: '#ffffff',
  accent: '#ff9900',
  positive: '#00c176',
  negative: '#ff3b3b',
  neutral: '#4a90d9',
  chartPrimary: '#ff9900',
  chartSecondary: '#4a90d9',
  chartTertiary: '#00c176',
  chart6mo: '#9b59b6',
  chart12mo: '#555555',
} as const;

// Per-source colors so Bloomberg / MarketWatch / FT etc. are distinguishable.
// Curated brand-ish colors for the major publications; everything else gets a
// stable color hashed from its name.
const SOURCE_BRAND: { match: string; color: string }[] = [
  { match: 'bloomberg', color: '#e67e22' },        // orange
  { match: 'wsj', color: '#5b8ff9' },              // WSJ blue
  { match: 'wall street journal', color: '#5b8ff9' },
  { match: 'financial times', color: '#f4978e' },  // FT salmon
  { match: 'ft alphaville', color: '#f4978e' },
  { match: 'ft markets', color: '#f4978e' },
  { match: 'ft global', color: '#f4978e' },
  { match: 'marketwatch', color: '#2ecc71' },      // green
  { match: 'cnbc', color: '#4a90d9' },             // blue
  { match: 'nyt', color: '#c9c9c9' },              // light gray
  { match: 'new york times', color: '#c9c9c9' },
  { match: 'securitization', color: '#1abc9c' },   // teal
];

const SOURCE_PALETTE = [
  '#9b59b6', '#e84393', '#1abc9c', '#f1c40f', '#3498db',
  '#e74c3c', '#16a085', '#d35400', '#7f8cff', '#27ae60', '#a569bd',
];

export function sourceColor(feedName: string): string {
  const n = (feedName || '').toLowerCase();
  for (const b of SOURCE_BRAND) {
    if (n.includes(b.match)) return b.color;
  }
  let h = 0;
  for (let i = 0; i < n.length; i++) h = (h * 31 + n.charCodeAt(i)) >>> 0;
  return SOURCE_PALETTE[h % SOURCE_PALETTE.length];
}
