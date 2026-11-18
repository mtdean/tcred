// Consumer segments — the Stress Lab scenario by borrower age, credit score,
// neighbourhood income and state (macrobot's `consumer_segments` model).
// A state can take an extra local shock on top of the national scenario.

import { useMemo, useState } from 'react';
import { COLORS } from '../../lib/colors';
import {
  stressResponse,
  type AbfClass,
  type ConsumerGrid,
  type RegionalShocks,
  type StressInputs,
  type StressKit,
} from '../../lib/stressEngine';

const signed = (x: number, dp = 0, unit = '') => `${x >= 0 ? '+' : ''}${x.toFixed(dp)}${unit}`;
type Out = Record<string, { delta: number; stressed: number | null }>;
const head = (t: string) => (
  <div className="muted" style={{ fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase' }}>{t}</div>
);

function SegmentGrid({ kit, grid, out }: { kit: StressKit; grid: ConsumerGrid; out: Out }) {
  const cls = Object.fromEntries(kit.consumer!.classes.map((x) => [x.key, x]));
  const keys = grid.rows.flatMap((r) => grid.cols.map((c) => `${grid.prefix}_${r.key}_${c.key}`));
  const maxDelta = Math.max(...keys.map((k) => Math.abs(out[k]?.delta ?? 0)), 0.5);
  return (
    <div>
      {head(`${grid.title} · ${grid.measure}`)}
      <div style={{ display: 'grid', gridTemplateColumns: `minmax(90px, 0.9fr) repeat(${grid.cols.length}, 1fr)`, gap: 3, marginTop: 6 }}>
        <span />
        {grid.cols.map((b) => (
          <div key={b.key} className="muted" style={{ fontSize: 9, letterSpacing: '0.1em', textAlign: 'center', textTransform: 'uppercase' }}>{b.label}</div>
        ))}
        {grid.rows.map((p) => (
          <div key={p.key} style={{ display: 'contents' }}>
            <span style={{ fontSize: 11, color: COLORS.textSecondary, alignSelf: 'center' }}>{p.label}</span>
            {grid.cols.map((b) => {
              const key = `${grid.prefix}_${p.key}_${b.key}`;
              const spec: AbfClass | undefined = cls[key];
              const r = out[key];
              if (!spec || !r) return <div key={b.key} />;
              const tone = r.delta >= 0 ? COLORS.negative : COLORS.positive;
              const strength = Math.min(Math.abs(r.delta) / maxDelta, 1);
              return (
                <div key={b.key}
                  title={`${spec.label}: ${spec.measure}. Latest ${spec.level?.toFixed(2)}%, average ${spec.mean?.toFixed(2)}%. ${spec.usable ? `R² ${spec.r2.toFixed(2)}, ${spec.n} quarters` : `No measurable link to the macro shock (R² ${spec.r2.toFixed(2)}) — held at baseline`}`}
                  style={{ padding: '6px 4px', textAlign: 'center', border: `1px solid ${COLORS.border}`, fontVariantNumeric: 'tabular-nums',
                    background: `color-mix(in srgb, ${tone} ${Math.round(strength * 45)}%, ${COLORS.bgPanel})`, opacity: spec.usable ? 1 : 0.55 }}>
                  <div style={{ fontSize: 14, fontWeight: 700, color: COLORS.textPrimary }}>{r.stressed == null ? '—' : `${r.stressed.toFixed(1)}%`}</div>
                  <div style={{ fontSize: 9, color: COLORS.textSecondary }}>
                    {spec.usable ? `${spec.level?.toFixed(1)}% ${signed(r.delta, 2, 'pp')}` : 'no macro link'}
                  </div>
                </div>
              );
            })}
          </div>
        ))}
      </div>
      {grid.note && <div className="muted" style={{ fontSize: 10, marginTop: 5, lineHeight: 1.4 }}>{grid.note}</div>}
    </div>
  );
}

function StateRows({ kit, out, selected, onSelect, regional, suffix }: {
  kit: StressKit; out: Out; suffix: string;
  selected: string; onSelect: (code: string) => void; regional: RegionalShocks;
}) {
  const c = kit.consumer!;
  const cls = Object.fromEntries(c.classes.map((x) => [x.key, x]));
  const rows = c.states
    .map((s) => {
      const lc = s.code.toLowerCase();
      return { s, spec: cls[`st_${lc}${suffix}`], r: out[`st_${lc}${suffix}`], u: out[`st_${lc}_u`]?.delta ?? 0, h: out[`st_${lc}_hpi`]?.delta ?? 0 };
    })
    .filter((x) => x.spec && x.r)
    .sort((a, b) => b.r.delta - a.r.delta);
  const scale = Math.max(...rows.map((x) => Math.max(x.spec.level ?? 0, x.r.stressed ?? 0)), 0.01) * 1.05;
  return (
    <div style={{ maxHeight: 470, overflowY: 'auto' }}>
      {rows.map(({ s, spec, r, u, h }) => {
        const on = s.code === selected;
        const local = regional[s.code];
        const hasLocal = !!local && (Math.abs(local.unrate_pp ?? 0) > 1e-9 || Math.abs(local.hpi_pct ?? 0) > 1e-9);
        const color = Math.abs(r.delta) < 1e-9 ? COLORS.textSecondary : r.delta > 0 ? COLORS.negative : COLORS.positive;
        const hKey = `h_${s.code.toLowerCase()}`;
        const hpiDead = hKey in spec.coefs && Math.abs(spec.coefs[hKey]) < 1e-12;
        return (
          <button key={s.code} type="button" onClick={() => onSelect(s.code)}
            title={`${spec.measure} · R² ${spec.r2.toFixed(2)} · average ${spec.mean?.toFixed(2)}%${hpiDead ? ' · home prices add nothing beyond unemployment in this state' : ''}`}
            style={{ display: 'grid', gridTemplateColumns: 'minmax(110px, 0.8fr) minmax(170px, 1fr) minmax(140px, 2fr) 110px', gap: 10, alignItems: 'center', width: '100%', textAlign: 'left',
              padding: '5px 6px', background: on ? COLORS.bgPanel : 'transparent', border: 'none', borderLeft: `2px solid ${on ? COLORS.accent : 'transparent'}`,
              borderBottom: `1px solid ${COLORS.border}`, cursor: 'pointer', font: 'inherit', color: 'inherit' }}>
            <span style={{ fontSize: 11, color: on ? COLORS.accent : COLORS.textPrimary }}>
              {s.name}{hasLocal && <span style={{ color: COLORS.accent }}> ●</span>}
            </span>
            <span className="muted" style={{ fontSize: 10, fontVariantNumeric: 'tabular-nums' }}>
              unemp {signed(u, 1, 'pp')} · homes {signed(h, 0, '%')}
            </span>
            <div>
              <div style={{ height: 4, background: COLORS.border }}>
                <div style={{ height: 4, width: `${((spec.level ?? 0) / scale) * 100}%`, background: COLORS.textSecondary }} />
              </div>
              <div style={{ height: 4, background: COLORS.border, marginTop: 2 }}>
                <div style={{ height: 4, width: `${Math.min(((r.stressed ?? 0) / scale) * 100, 100)}%`, background: color }} />
              </div>
            </div>
            <span style={{ fontSize: 11, textAlign: 'right', fontVariantNumeric: 'tabular-nums', color }}>
              {spec.level?.toFixed(1)}% → {r.stressed?.toFixed(1)}%
            </span>
          </button>
        );
      })}
    </div>
  );
}

// Metro areas: the most exposed under the scenario, with a name / state filter.
function MetroSection({ kit, out }: { kit: StressKit; out: Record<string, Record<string, number>> }) {
  const metros = kit.consumer?.metros;
  const [measure, setMeasure] = useState<string>(metros?.measures[0]?.key ?? '');
  const [query, setQuery] = useState('');
  const [showAll, setShowAll] = useState(false);
  if (!metros || !metros.rows.length) return null;
  const m = metros.measures.find((x) => x.key === measure) ?? metros.measures[0];
  const q = query.trim().toLowerCase();
  const rows = metros.rows
    .filter((r) => r.measures[m.key] && (!q || r.name.toLowerCase().includes(q)))
    .map((r) => ({ r, v: r.measures[m.key], d: out[r.code]?.[m.key] ?? 0 }))
    .sort((a, b) => b.d - a.d || b.v.level - a.v.level);
  const shown = showAll || q ? rows : rows.slice(0, 15);
  const scale = Math.max(...rows.map((x) => Math.max(x.v.level, x.v.level + x.d)), 0.01) * 1.05;
  const btn = (on: boolean) => ({
    background: 'transparent', color: on ? COLORS.accent : COLORS.textSecondary, border: `1px solid ${on ? COLORS.accent : COLORS.borderBright}`,
    fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase' as const, padding: '2px 8px', cursor: 'pointer',
  });
  return (
    <div>
      <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap', marginTop: 6 }}>
        {head(`By metro area (${metros.rows.length}) ·`)}
        {metros.measures.map((x) => (
          <button key={x.key} type="button" onClick={() => setMeasure(x.key)} style={btn(x.key === m.key)}>{x.label}</button>
        ))}
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="filter: city or state (e.g. TX)"
          style={{ background: COLORS.bgPanel, border: `1px solid ${COLORS.border}`, color: COLORS.textPrimary, font: 'inherit', fontSize: 10, padding: '2px 8px', width: 190 }} />
        <span className="muted" style={{ fontSize: 10 }}>{m.measure} · {q ? `${rows.length} match` : showAll ? 'all, most exposed first' : '15 most exposed'}</span>
        {!q && <button type="button" onClick={() => setShowAll((v) => !v)} style={btn(false)}>{showAll ? 'top 15' : 'show all'}</button>}
      </div>
      <div style={{ maxHeight: 420, overflowY: 'auto', marginTop: 6 }}>
        {shown.map(({ r, v, d }) => {
          const color = Math.abs(d) < 1e-9 ? COLORS.textSecondary : d > 0 ? COLORS.negative : COLORS.positive;
          return (
            <div key={r.code} title={`R² ${v.r2.toFixed(2)}${v.usable ? '' : ' — no measurable link to the national cycle, held at baseline'}`}
              style={{ display: 'grid', gridTemplateColumns: 'minmax(180px, 1.4fr) minmax(140px, 2fr) 150px', gap: 10, alignItems: 'center', padding: '4px 6px', borderBottom: `1px solid ${COLORS.border}`, opacity: v.usable ? 1 : 0.55 }}>
              <span style={{ fontSize: 11, color: COLORS.textPrimary, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.name}</span>
              <div>
                <div style={{ height: 4, background: COLORS.border }}><div style={{ height: 4, width: `${(v.level / scale) * 100}%`, background: COLORS.textSecondary }} /></div>
                <div style={{ height: 4, background: COLORS.border, marginTop: 2 }}><div style={{ height: 4, width: `${Math.min((Math.max(v.level + d, 0) / scale) * 100, 100)}%`, background: color }} /></div>
              </div>
              <span style={{ fontSize: 11, textAlign: 'right', fontVariantNumeric: 'tabular-nums', color }}>
                {v.usable ? `${v.level.toFixed(1)}% → ${Math.max(v.level + d, 0).toFixed(1)}%` : `${v.level.toFixed(1)}% · no link`}
              </span>
            </div>
          );
        })}
      </div>
      <div className="muted" style={{ fontSize: 10, marginTop: 5, lineHeight: 1.4 }}>
        Each metro&apos;s own history against national unemployment (and national home prices for mortgages). No local shock here — use the state rows for that. Small metros are noisy.
      </div>
    </div>
  );
}

export default function ConsumerSegmentsView({ kit, inputs, phase }: { kit: StressKit; inputs: StressInputs; phase: number }) {
  const c = kit.consumer;
  const [selected, setSelected] = useState<string>(c?.states[0]?.code ?? '');
  const [measure, setMeasure] = useState<string>(c?.state_measures[0]?.key ?? '');
  const [regional, setRegional] = useState<RegionalShocks>({});
  const res = useMemo(() => stressResponse(kit, inputs, phase, regional), [kit, inputs, phase, regional]);
  if (!c) {
    return <div className="muted" style={{ fontSize: 11 }}>No consumer segment estimates in this bundle — run macrobot&apos;s consumer_segments model.</div>;
  }
  const out = res.consumer;
  const sel = c.states.find((s) => s.code === selected);
  const local = regional[selected] ?? {};
  const setLocal = (key: 'unrate_pp' | 'hpi_pct', v: number) =>
    setRegional((p) => ({ ...p, [selected]: { ...p[selected], [key]: v } }));
  const sm = c.state_measures.find((m) => m.key === measure) ?? c.state_measures[0];
  const anyLocal = Object.values(regional).some((r) => Math.abs(r.unrate_pp ?? 0) > 1e-9 || Math.abs(r.hpi_pct ?? 0) > 1e-9);
  return (
    <div className="stack">
      {c.grids.map((g) => <SegmentGrid key={g.key} kit={kit} grid={g} out={out} />)}
      {c.mix.map((k) => {
        const spec = c.classes.find((x) => x.key === k);
        const r = out[k];
        if (!spec || !r) return null;
        return (
          <div key={k} style={{ fontSize: 11, border: `1px solid ${COLORS.border}`, padding: '6px 10px', background: COLORS.bgPanel }}>
            <span className="muted" style={{ fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase', marginRight: 10 }}>{spec.label}</span>
            <span style={{ color: COLORS.textPrimary, fontVariantNumeric: 'tabular-nums' }}>
              {spec.level?.toFixed(1)}% → <span style={{ color: r.delta > 1e-9 ? COLORS.negative : COLORS.textPrimary, fontWeight: 700 }}>{r.stressed?.toFixed(1)}%</span>
            </span>
            <span className="muted" style={{ marginLeft: 10, fontSize: 10 }}>of consumers · migration into subprime as unemployment rises (R² {spec.r2.toFixed(2)})</span>
          </div>
        );
      })}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 6, gap: 10, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
          {head('By state ·')}
          {c.state_measures.map((m) => (
            <button key={m.key} type="button" onClick={() => setMeasure(m.key)}
              style={{ background: 'transparent', color: m.key === sm?.key ? COLORS.accent : COLORS.textSecondary, border: `1px solid ${m.key === sm?.key ? COLORS.accent : COLORS.borderBright}`,
                fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase', padding: '2px 8px', cursor: 'pointer' }}>
              {m.label}
            </button>
          ))}
          <span className="muted" style={{ fontSize: 10 }}>{sm?.measure} · grey = latest · colour = your scenario · click a state to shock it</span>
        </div>
        {anyLocal && (
          <button type="button" onClick={() => setRegional({})}
            style={{ background: 'transparent', border: `1px solid ${COLORS.borderBright}`, color: COLORS.textSecondary, fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase', padding: '2px 8px', cursor: 'pointer' }}>
            clear local shocks
          </button>
        )}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 3fr) minmax(220px, 1fr)', gap: 14, alignItems: 'start' }}>
        <StateRows kit={kit} out={out} selected={selected} onSelect={setSelected} regional={regional} suffix={sm?.suffix ?? ''} />
        {sel && (
          <div style={{ border: `1px solid ${COLORS.border}`, padding: '8px 12px', background: COLORS.bgPanel }}>
            <div className="muted" style={{ fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase' }}>Local shock · {sel.name}</div>
            <div className="muted" style={{ fontSize: 10, margin: '3px 0 8px' }}>
              on top of the national scenario{sel.unrate != null && <> · unemployment now {sel.unrate.toFixed(1)}%</>}
            </div>
            {c.regional_inputs.map((spec) => {
              const key = spec.key as 'unrate_pp' | 'hpi_pct';
              const v = local[key] ?? spec.default;
              const moved = Math.abs(v - spec.default) > 1e-9;
              return (
                <label key={spec.key} style={{ display: 'block', marginBottom: 10 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11 }}>
                    <span style={{ color: moved ? COLORS.textPrimary : COLORS.textSecondary }}>{spec.label}</span>
                    <span style={{ color: moved ? COLORS.accent : COLORS.textDim, fontVariantNumeric: 'tabular-nums' }}>{signed(v, spec.unit === 'pp' ? 2 : 0, spec.unit)}</span>
                  </div>
                  <input type="range" min={spec.min} max={spec.max} step={spec.step} value={v}
                    onChange={(e) => setLocal(key, Number(e.target.value))}
                    onDoubleClick={() => setLocal(key, spec.default)}
                    style={{ width: '100%', accentColor: COLORS.accent }} />
                </label>
              );
            })}
          </div>
        )}
      </div>
      <MetroSection kit={kit} out={res.metros} />
      <div className="muted" style={{ fontSize: 10, border: `1px solid ${COLORS.border}`, padding: '6px 10px', background: COLORS.bgPanel, lineHeight: 1.5 }}>
        Every lender&apos;s borrowers (NY Fed Consumer Credit Panel; state, score and income cuts via the Philadelphia Fed Consumer Credit Explorer), not a pool.
        Flow and stock measures are not comparable in level. A national shock reaches a state through how its unemployment and home prices have moved
        with the nation&apos;s; a local shock adds directly. Where home prices added nothing beyond unemployment the home-price slider does not move that
        state (hover a row). Dimmed cells showed no measurable link to the economy and stay at baseline. Not modelled:{' '}
        {c.not_modelled.map((m, i) => (
          <span key={m.label}>{i > 0 && ' · '}<span style={{ color: COLORS.textSecondary }}>{m.label}</span> ({m.reason})</span>
        ))}
      </div>
    </div>
  );
}
