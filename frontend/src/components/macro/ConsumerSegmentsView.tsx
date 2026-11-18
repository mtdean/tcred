// Consumer segments — the Stress Lab scenario by borrower age band and by
// state (macrobot's `consumer_segments` model, NY Fed Consumer Credit Panel).
// A state can take an extra local shock on top of the national scenario.

import { useMemo, useState } from 'react';
import { COLORS } from '../../lib/colors';
import {
  stressResponse,
  type AbfClass,
  type RegionalShocks,
  type StressInputs,
  type StressKit,
} from '../../lib/stressEngine';

const signed = (x: number, dp = 0, unit = '') => `${x >= 0 ? '+' : ''}${x.toFixed(dp)}${unit}`;
const head = (t: string) => (
  <div className="muted" style={{ fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase' }}>{t}</div>
);

function AgeGrid({ kit, out }: { kit: StressKit; out: Record<string, { delta: number; stressed: number | null }> }) {
  const c = kit.consumer!;
  const cls = Object.fromEntries(c.classes.map((x) => [x.key, x]));
  const maxDelta = Math.max(...c.classes.filter((x) => x.key.startsWith('age_')).map((x) => Math.abs(out[x.key]?.delta ?? 0)), 0.5);
  return (
    <div style={{ display: 'grid', gridTemplateColumns: `minmax(90px, 0.9fr) repeat(${c.age.bands.length}, 1fr)`, gap: 3 }}>
      <span />
      {c.age.bands.map((b) => (
        <div key={b.key} className="muted" style={{ fontSize: 9, letterSpacing: '0.1em', textAlign: 'center' }}>AGE {b.label}</div>
      ))}
      {c.age.products.map((p) => (
        <div key={p.key} style={{ display: 'contents' }}>
          <span style={{ fontSize: 11, color: COLORS.textSecondary, alignSelf: 'center' }}>{p.label}</span>
          {c.age.bands.map((b) => {
            const key = `age_${p.key}_${b.key}`;
            const spec: AbfClass | undefined = cls[key];
            const r = out[key];
            if (!spec || !r) return <div key={b.key} />;
            const tone = r.delta >= 0 ? COLORS.negative : COLORS.positive;
            const strength = Math.min(Math.abs(r.delta) / maxDelta, 1);
            return (
              <div key={b.key}
                title={`${spec.label}: ${spec.measure}. Latest ${spec.level?.toFixed(2)}%, average ${spec.mean?.toFixed(2)}%. ${spec.usable ? `R² ${spec.r2.toFixed(2)}` : `No measurable link to the macro shock (R² ${spec.r2.toFixed(2)}) — held at baseline`}`}
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
  );
}

function StateRows({ kit, out, selected, onSelect, regional }: {
  kit: StressKit; out: Record<string, { delta: number; stressed: number | null }>;
  selected: string; onSelect: (code: string) => void; regional: RegionalShocks;
}) {
  const c = kit.consumer!;
  const cls = Object.fromEntries(c.classes.map((x) => [x.key, x]));
  const rows = c.states
    .map((s) => {
      const lc = s.code.toLowerCase();
      return { s, spec: cls[`st_${lc}`], r: out[`st_${lc}`], u: out[`st_${lc}_u`]?.delta ?? 0, h: out[`st_${lc}_hpi`]?.delta ?? 0 };
    })
    .filter((x) => x.spec && x.r)
    .sort((a, b) => b.r.delta - a.r.delta);
  const scale = Math.max(...rows.map((x) => Math.max(x.spec.level ?? 0, x.r.stressed ?? 0)), 0.01) * 1.05;
  return (
    <div>
      {rows.map(({ s, spec, r, u, h }) => {
        const on = s.code === selected;
        const local = regional[s.code];
        const hasLocal = !!local && (Math.abs(local.unrate_pp ?? 0) > 1e-9 || Math.abs(local.hpi_pct ?? 0) > 1e-9);
        const color = Math.abs(r.delta) < 1e-9 ? COLORS.textSecondary : r.delta > 0 ? COLORS.negative : COLORS.positive;
        const hpiDead = Math.abs(spec.coefs[`h_${s.code.toLowerCase()}`] ?? 0) < 1e-12;
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

export default function ConsumerSegmentsView({ kit, inputs, phase }: { kit: StressKit; inputs: StressInputs; phase: number }) {
  const c = kit.consumer;
  const [selected, setSelected] = useState<string>(c?.states[0]?.code ?? '');
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
  const anyLocal = Object.values(regional).some((r) => Math.abs(r.unrate_pp ?? 0) > 1e-9 || Math.abs(r.hpi_pct ?? 0) > 1e-9);
  return (
    <div className="stack">
      {head('Flow into 90+ days late by borrower age · 12 months under your scenario · latest reading and change beneath')}
      <AgeGrid kit={kit} out={out} />
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginTop: 6 }}>
        {head('Flow into 90+ days late by state, all debt · grey = latest · colour = your scenario · click a state to shock it')}
        {anyLocal && (
          <button type="button" onClick={() => setRegional({})}
            style={{ background: 'transparent', border: `1px solid ${COLORS.borderBright}`, color: COLORS.textSecondary, fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase', padding: '2px 8px', cursor: 'pointer' }}>
            clear local shocks
          </button>
        )}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 3fr) minmax(220px, 1fr)', gap: 14, alignItems: 'start' }}>
        <StateRows kit={kit} out={out} selected={selected} onSelect={setSelected} regional={regional} />
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
      <div className="muted" style={{ fontSize: 10, border: `1px solid ${COLORS.border}`, padding: '6px 10px', background: COLORS.bgPanel, lineHeight: 1.5 }}>
        Every lender&apos;s borrowers (NY Fed Consumer Credit Panel), not a pool. A national shock reaches a state through how its unemployment and home
        prices have moved with the nation&apos;s; a local shock adds directly. Where home prices added nothing beyond unemployment, the home-price slider
        does not move that state (hover a row). Dimmed age cells showed no measurable link to the economy and stay at baseline. Not modelled:{' '}
        {c.not_modelled.map((m, i) => (
          <span key={m.label}>{i > 0 && ' · '}<span style={{ color: COLORS.textSecondary }}>{m.label}</span> ({m.reason})</span>
        ))}
      </div>
    </div>
  );
}
