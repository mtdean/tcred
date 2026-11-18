// ABF regime playbook — which generic structure pays the most spread for the
// cushion each regime uses up. Precomputed by macrobot (`abf_playbook` in the
// bundle) from the desk's spread file and the Stress Lab's tranche headroom.

import { useState } from 'react';
import { COLORS } from '../../lib/colors';
import type { MacroViews } from '../../lib/types';
import Panel from '../shared/Panel';

export interface PlaybookTranche {
  rating: string; spread_bp: number; coupon: string; source: string; as_of: string;
  used: number; status: string; avoid: boolean; score: number;
}
export interface PlaybookStructure {
  key: string; label: string; coupon: string; extension: string; wal_years: number;
  rate_mark_pct: number | null; coupon_change_bp: number | null; modelled: boolean;
  pick: string | null; score: number; tranches: PlaybookTranche[];
}
export interface PlaybookRegime {
  name: string; label: string; stance: string;
  d_10y_bp: number; d_policy_bp: number; d_unrate_pp: number;
  structures: PlaybookStructure[];
}
export interface AbfPlaybook {
  version: number; floor_used_pp: number; avoid_statuses: string[];
  spread_sources: string[]; spreads_as_of: string; all_generic: boolean;
  regimes: PlaybookRegime[];
}

const STATUS_COLOR: Record<string, string> = {
  intact: COLORS.positive, eroding: COLORS.accent, 'at risk': COLORS.negative, loss: COLORS.negative,
};
const EXT_COLOR: Record<string, string> = {
  low: COLORS.textDim, medium: COLORS.accent, high: COLORS.negative,
};
const SHORT: Record<string, string> = {
  baseline: 'Baseline', rate_shock: 'Rate shock', inflation_spike: 'Inflation',
  recession: 'Recession', risk_off: 'Risk-off', soft_landing: 'Soft landing',
};

const signed = (v: number, d = 0, unit = '') => `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(d)}${unit}`;

function Chip({ text, color, title }: { text: string; color: string; title?: string }) {
  return (
    <span title={title} style={{ border: `1px solid ${color}`, color, fontSize: 9, padding: '0 5px', letterSpacing: '0.06em', whiteSpace: 'nowrap' }}>
      {text}
    </span>
  );
}

function StructureRow({ s, rank, max }: { s: PlaybookStructure; rank: number | null; max: number }) {
  const pick = s.tranches.find((t) => t.rating === s.pick);
  const rateChip = s.coupon === 'floating'
    ? { text: `FLOAT · coupon ${signed(s.coupon_change_bp ?? 0, 0, 'bp')}`, color: (s.coupon_change_bp ?? 0) < -1 ? COLORS.accent : COLORS.textSecondary }
    : { text: `FIXED · price ${signed(s.rate_mark_pct ?? 0, 1, '%')}`, color: (s.rate_mark_pct ?? 0) < -0.05 ? COLORS.negative : (s.rate_mark_pct ?? 0) > 0.05 ? COLORS.positive : COLORS.textSecondary };
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '22px minmax(150px, 1.1fr) minmax(120px, 1.4fr) minmax(200px, 1.6fr)', gap: 10, alignItems: 'center', padding: '6px 0', borderBottom: `1px solid ${COLORS.border}`, opacity: s.pick ? 1 : 0.6 }}>
      <span style={{ color: COLORS.textDim, fontSize: 11, fontVariantNumeric: 'tabular-nums' }}>{rank ?? '—'}</span>
      <div>
        <div style={{ fontSize: 11, color: COLORS.textPrimary }}>{s.label}</div>
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 3 }}>
          <Chip {...rateChip} title={s.coupon === 'floating' ? 'Coupon moves with the policy rate' : `Price mark ≈ −WAL (${s.wal_years}y) × change in the 10-year`} />
          <Chip text={`EXT ${s.extension.toUpperCase()}`} color={EXT_COLOR[s.extension] ?? COLORS.textDim} title="Extension risk: how much the deal's life lengthens when prepayments slow or loans are extended" />
        </div>
      </div>
      <div>
        {pick ? (
          <>
            <div style={{ height: 8, background: COLORS.border }}>
              <div style={{ height: 8, width: `${Math.min(s.score / max, 1) * 100}%`, background: COLORS.accent }} />
            </div>
            <div style={{ fontSize: 10, marginTop: 3, color: COLORS.textSecondary, fontVariantNumeric: 'tabular-nums' }}>
              <span style={{ color: COLORS.textPrimary }}>{pick.rating}</span> · {pick.spread_bp.toFixed(0)}bp · {(Math.max(pick.used, 0) * 100).toFixed(0)}% used · <span style={{ color: COLORS.accent }}>{s.score.toFixed(0)} bp/pt</span>
            </div>
          </>
        ) : (
          <div className="muted" style={{ fontSize: 10 }}>
            {s.modelled ? 'every rated tranche at risk — no pick' : 'no loss model for this asset class — not ranked'}
          </div>
        )}
      </div>
      <div style={{ display: 'flex', gap: 3 }}>
        {s.tranches.map((t) => {
          const c = STATUS_COLOR[t.status] ?? COLORS.textSecondary;
          const isPick = t.rating === s.pick;
          return (
            <div key={t.rating} title={`${t.rating}: ${t.spread_bp.toFixed(0)}bp (${t.source}${t.as_of ? `, ${t.as_of}` : ''}) · ${(t.used * 100).toFixed(0)}% of headroom used · ${t.status}`}
              style={{ flex: 1, minWidth: 0, textAlign: 'center', fontSize: 9, padding: '2px 0', border: `1px solid ${isPick ? COLORS.accent : COLORS.border}`, borderBottom: `3px solid ${c}`, color: t.avoid ? COLORS.textDim : COLORS.textSecondary, fontVariantNumeric: 'tabular-nums' }}>
              <div style={{ color: isPick ? COLORS.accent : undefined }}>{t.rating}</div>
              <div>{t.spread_bp.toFixed(0)}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// Rank of each structure in each regime, as a colour grid.
function RankGrid({ pb, active, onPick }: { pb: AbfPlaybook; active: string; onPick: (n: string) => void }) {
  const base = pb.regimes[0].structures;
  const rankIn = (r: PlaybookRegime, key: string) => {
    const i = r.structures.findIndex((s) => s.key === key);
    return i >= 0 && r.structures[i].pick ? i + 1 : null;
  };
  const n = base.length;
  return (
    <div style={{ display: 'grid', gridTemplateColumns: `minmax(150px, 1.3fr) repeat(${pb.regimes.length}, 1fr)`, gap: 2, fontSize: 10 }}>
      <span />
      {pb.regimes.map((r) => (
        <button key={r.name} onClick={() => onPick(r.name)}
          style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase', padding: '2px 0', color: r.name === active ? COLORS.accent : COLORS.textSecondary }}>
          {SHORT[r.name] ?? r.name}
        </button>
      ))}
      {base.map((s) => (
        <div key={s.key} style={{ display: 'contents' }}>
          <span style={{ color: COLORS.textSecondary, alignSelf: 'center' }}>{s.label}</span>
          {pb.regimes.map((r) => {
            const rank = rankIn(r, s.key);
            const strength = rank ? 1 - (rank - 1) / Math.max(n - 1, 1) : 0;
            return (
              <div key={r.name} title={rank ? `#${rank} of ${n} in ${r.label}` : 'not ranked'}
                style={{ textAlign: 'center', padding: '3px 0', fontVariantNumeric: 'tabular-nums', outline: r.name === active ? `1px solid ${COLORS.border}` : undefined,
                  background: rank ? `color-mix(in srgb, ${COLORS.accent} ${Math.round(strength * 55)}%, transparent)` : 'transparent',
                  color: rank ? COLORS.textPrimary : COLORS.textDim }}>
                {rank ?? '—'}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

export default function AbfPlaybookPanel({ views, initialRegime }: { views: MacroViews; initialRegime?: string | null }) {
  const pb = views.abf_playbook;
  const [name, setName] = useState<string>(initialRegime ?? 'recession');
  if (!pb || !pb.regimes.length) {
    return (
      <Panel title="ABF regime playbook">
        <div className="muted" style={{ fontSize: 11 }}>No playbook in this bundle — re-run macrobot&apos;s run_synthesis.</div>
      </Panel>
    );
  }
  const regime = pb.regimes.find((r) => r.name === name) ?? pb.regimes[0];
  const max = Math.max(...regime.structures.map((s) => s.score), 1);
  let rank = 0;
  return (
    <Panel title="ABF regime playbook" subtitle="Spread earned per point of tranche cushion the regime uses up — generic structures, your spread marks">
      <div className="stack">
        {pb.all_generic && (
          <div style={{ fontSize: 10, border: `1px solid ${COLORS.accent}`, color: COLORS.accent, padding: '5px 10px', lineHeight: 1.5 }}>
            Every spread here is a seeded placeholder, not a market level. Put your marks in macrobot&apos;s data/desk/abf_spreads.csv and re-run run_synthesis; the ranking is only as good as that file.
          </div>
        )}
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {pb.regimes.map((r) => (
            <button key={r.name} onClick={() => setName(r.name)}
              style={{ background: r.name === regime.name ? COLORS.accent : 'transparent', color: r.name === regime.name ? COLORS.bgPanel : COLORS.textSecondary, border: `1px solid ${r.name === regime.name ? COLORS.accent : COLORS.border}`, fontSize: 10, letterSpacing: '0.08em', textTransform: 'uppercase', padding: '3px 10px', cursor: 'pointer' }}>
              {SHORT[r.name] ?? r.name}
            </button>
          ))}
        </div>
        <div style={{ fontSize: 11, color: COLORS.textPrimary }}>
          {regime.label}
          <span className="muted" style={{ marginLeft: 8, fontSize: 10 }}>
            unemployment {signed(regime.d_unrate_pp, 1, 'pp')} · policy rate {signed(regime.d_policy_bp, 0, 'bp')} · 10-year {signed(regime.d_10y_bp, 0, 'bp')}
          </span>
        </div>
        <div>
          {regime.structures.map((s) => (
            <StructureRow key={s.key} s={s} rank={s.pick ? ++rank : null} max={max} />
          ))}
        </div>
        <div className="muted" style={{ fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase', marginTop: 4 }}>
          Rank by regime · 1 = best paid for the cushion used
        </div>
        <RankGrid pb={pb} active={regime.name} onPick={setName} />
        <div className="muted" style={{ fontSize: 10, border: `1px solid ${COLORS.border}`, padding: '6px 10px', background: COLORS.bgPanel, lineHeight: 1.5 }}>
          Each deal is shown by its best tranche: the highest spread per point of headroom used (bp/pt), counting at least {pb.floor_used_pp.toFixed(0)} points
          so an untouched tranche does not score infinity. A tranche the regime puts at risk or into loss is never picked. Tranche boxes show
          rating and spread (bp); the underline is its status in this regime. Spreads do not move with the regime here, so this ranks carry
          against cushion, not mark-to-market. Fixed-rate price marks use WAL as duration. Spreads as of {pb.spreads_as_of || 'n/a'} ({pb.spread_sources.join(', ')}).
        </div>
      </div>
    </Panel>
  );
}
