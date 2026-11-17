// Floating-Rate Coverage — macrobot `floating_rate_credit`.
//
// Forward 1M Term SOFR (Pensford download, or Treasury forwards anchored to the
// live Pensford fixing) → direct-lending coupon → share of borrowers whose
// EBITDA can't cover cash interest, baseline vs stress. Tiles + two charts.

import { useQuery } from '@tanstack/react-query';
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { getMacroViews } from '../../lib/api';
import { COLORS } from '../../lib/colors';
import type { FloatingRatePoint } from '../../lib/types';
import Panel from '../shared/Panel';
import LoadingCursor from '../shared/LoadingCursor';
import TooltipShell from '../charts/TooltipShell';
import DocLink from '../macro/DocLink';

const pct = (x: number | null | undefined, d = 2) => (x == null ? '—' : `${x.toFixed(d)}%`);
const share = (x: number | null | undefined) => (x == null ? '—' : `${(x * 100).toFixed(1)}%`);

function Tile({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div style={{ border: `1px solid ${COLORS.border}`, padding: '8px 12px', flex: '1 1 150px',
      background: COLORS.bgPanel }}>
      <div className="muted" style={{ fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase' }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700, marginTop: 2, color: color ?? COLORS.textPrimary }}>{value}</div>
      {sub && <div className="muted" style={{ fontSize: 10, marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

function PathChart({ path, lines, fmt }: {
  path: FloatingRatePoint[];
  lines: { key: keyof FloatingRatePoint; name: string; color: string; dash?: string }[];
  fmt: (v: number) => string;
}) {
  return (
    <ResponsiveContainer width="100%" height={200}>
      <LineChart data={path} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={COLORS.border} strokeDasharray="2 4" />
        <XAxis dataKey="h" tick={{ fill: COLORS.textSecondary, fontSize: 10 }}
          tickFormatter={(h) => `${h}m`} stroke={COLORS.border} />
        <YAxis tick={{ fill: COLORS.textSecondary, fontSize: 10 }} stroke={COLORS.border}
          tickFormatter={fmt} width={48} domain={['auto', 'auto']} />
        <Tooltip content={({ active, payload, label }) =>
          active && payload?.length ? (
            <TooltipShell title={`+${label}m`}>
              {payload.map((p) => (
                <div key={String(p.dataKey)} style={{ color: p.color }}>
                  {p.name}: {fmt(Number(p.value))}
                </div>
              ))}
            </TooltipShell>
          ) : null} />
        {lines.map((l) => (
          <Line key={String(l.key)} dataKey={l.key as string} name={l.name} stroke={l.color}
            strokeDasharray={l.dash} dot={false} strokeWidth={1.6} isAnimationActive={false} />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

export default function FloatingRatePanel() {
  const { data, isLoading } = useQuery({
    queryKey: ['macroViews'],
    queryFn: () => getMacroViews().then((r) => r.data),
    staleTime: 15 * 60_000,
  });
  if (isLoading) return <Panel title="Floating-Rate Coverage"><LoadingCursor /></Panel>;
  const fr = data?.floating_rate;
  if (!fr || fr.path.length === 0) return null;
  const now = fr.path[0];
  const h12 = fr.path.find((p) => p.h === 12) ?? fr.path[fr.path.length - 1];
  const meth = data?.methodology?.floating_rate;
  const fromPensford = fr.curve_source === 'pensford';
  return (
    <Panel
      title="Floating-Rate Coverage"
      subtitle={
        <span>
          forward 1M Term SOFR → coupon → borrower interest coverage ·{' '}
          <span style={{ color: fromPensford ? COLORS.positive : COLORS.accent }}>
            {fromPensford ? 'Pensford forward curve' : 'bias-corrected Treasury forwards @ Pensford spot'}
          </span>
          {meth && <> <DocLink m={meth} /></>}
        </span>
      }
    >
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginBottom: 12 }}>
        <Tile label="1M Term SOFR" value={pct(now.base_rate)} sub={`12m fwd ${pct(h12.base_rate)}`} />
        <Tile label="Loan coupon" value={pct(now.coupon)} sub={`12m fwd ${pct(h12.coupon)}`} />
        <Tile label="Median ICR" value={now.icr_median == null ? '—' : `${now.icr_median.toFixed(2)}x`}
          sub={`stress ${now.icr_median_stress == null ? '—' : `${now.icr_median_stress.toFixed(2)}x`}`} />
        <Tile label="Borrowers ICR < 1x" value={share(now.share_icr_lt_1)}
          sub={`12m ${share(h12.share_icr_lt_1)} · stress ${share(h12.share_icr_lt_1_stress)}`}
          color={COLORS.negative} />
        <Tile label="Borrowers ICR < 1.5x" value={share(now.share_icr_lt_1p5)}
          sub={`stress ${share(h12.share_icr_lt_1p5_stress)}`} color={COLORS.accent} />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 16 }}>
        <div>
          <div className="muted" style={{ fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase' }}>
            Base rate & coupon path (% p.a.)
          </div>
          <PathChart path={fr.path} fmt={(v) => `${v.toFixed(1)}%`} lines={[
            { key: 'base_rate', name: '1M Term SOFR', color: COLORS.chartSecondary },
            { key: 'coupon', name: 'Coupon (SOFR + spread)', color: COLORS.chartPrimary },
          ]} />
        </div>
        <div>
          <div className="muted" style={{ fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase' }}>
            Share of borrowers below coverage (baseline vs stress)
          </div>
          <PathChart path={fr.path} fmt={(v) => `${(v * 100).toFixed(0)}%`} lines={[
            { key: 'share_icr_lt_1', name: 'ICR < 1x', color: COLORS.negative },
            { key: 'share_icr_lt_1_stress', name: 'ICR < 1x (stress)', color: COLORS.negative, dash: '4 3' },
            { key: 'share_icr_lt_1p5', name: 'ICR < 1.5x', color: COLORS.accent },
            { key: 'share_icr_lt_1p5_stress', name: 'ICR < 1.5x (stress)', color: COLORS.accent, dash: '4 3' },
          ]} />
        </div>
      </div>
      <div className="muted" style={{ fontSize: 10, marginTop: 6 }}>
        Stylized book: SOFR + 525bp, 75bp floor, median 5.5x leverage (log-sd 0.45), EBITDA +3%/yr ·
        stress = SOFR +200bp & EBITDA −15%.
        {!fromPensford && (fr.pensford_curve_age_days != null
          ? ` The Pensford curve saved by ccflows-ui is ${Math.round(fr.pensford_curve_age_days)} days old, so it is not used — run ccflows-ui (its crawler refreshes daily) or drop an export in ~/macro-research/data/pensford/.`
          : ' No Pensford curve on disk — run ccflows-ui or drop an export in ~/macro-research/data/pensford/.')}
      </div>
    </Panel>
  );
}
