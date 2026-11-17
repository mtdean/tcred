// Client-side stress engine for the Forecasts → Stress Lab.
//
// A line-for-line mirror of macrobot's reference implementation
// (`macrobot/synthesis/stress_kit.py::stress_response`), run on the `stress_kit`
// block of the views bundle so sliders respond instantly with no backend call.
// `verifyKit` recomputes the preset scenarios and compares them with the answers
// macrobot's own scenario engine wrote into the kit — if the two ever drift, the
// panel shows it.

export interface StressInputSpec {
  key: string;
  label: string;
  unit: string;
  min: number;
  max: number;
  step: number;
  default: number;
}

export type StressInputs = Record<string, number>;
type Fan = { median: number[]; p16: number[]; p84: number[] };

export interface StressKit {
  version: number;
  inputs: StressInputSpec[];
  policy: {
    policy_per_core_pp: number;
    two_year_passthrough: number;
    ten_year_passthrough: number;
    unrate_per_100bp: number;
  };
  satellites: Record<string, number>;
  fair_value: {
    coef_logvix: number; coef_slope: number; coef_rate: number;
    vix_now: number; hy_fair_value: number;
  };
  stance: {
    evidence: Record<string, number | null>;
    thresholds: Record<string, number>;
  };
  total_return: {
    base_loss_pp: number;
    indices: Record<string, {
      carry: number; duration: number; d_rate_base_pp: number;
      d_spread_base_pp: number; loss_mult: number;
    }>;
  };
  paths: {
    h: number[];
    policy: number[] | null;
    ust10: number[] | null;
    hy: Fan | null;
    ig: Fan | null;
    corp_loss: number[] | null;
    card_loss: number[] | null;
    sofr: number[] | null;
  };
  floating: {
    spread_pct: number; sofr_floor_pct: number; leverage_median: number;
    leverage_log_sd: number; ebitda_growth_pct: number;
  } | null;
  presets: { name: string; label: string; inputs: StressInputs }[];
  checks: {
    name: string; inputs: StressInputs; expected: Record<string, number | string>;
    expected_abf?: Record<string, number>;
  }[];
  abf?: {
    classes: AbfClass[];
    not_modelled: { label: string; reason: string }[];
  } | null;
}

export interface AbfClass {
  key: string;
  label: string;
  kind: 'loss' | 'driver';
  target: string;
  measure: string;
  source: string;
  relevance: string;
  coefs: Record<string, number>;
  tstats: Record<string, number>;
  r2: number;
  n: number;
  usable: boolean;
  level: number | null;
}

// input → the ABF driver targets it adds to
const ABF_OVERLAYS: Record<string, string[]> = {
  hpi_pct: ['p_hpi'], car_pct: ['p_car'], oil_pct: ['p_oil', 'p_gas'],
};

export interface StressResult {
  abf: Record<string, { delta: number; stressed: number | null }>;
  deltas: Record<string, number>;
  stance: {
    stance: string; score: number;
    valuation: string; loss_cycle: string; volatility: string; funding: string;
  };
  total_return: Record<string, {
    total_return: number; carry: number; rate_pnl: number;
    spread_pnl: number; expected_loss: number;
  }>;
  paths: {
    h: number[];
    policy: number[] | null;
    ust10: number[] | null;
    hy: Fan | null;
    ig: Fan | null;
    corp_loss: number[] | null;
    card_loss: number[] | null;
    ust10_band?: { lo: number[]; hi: number[] };
    sofr?: number[];
    coupon?: number[];
    share_icr_lt_1?: number[];
  };
}

const HORIZON_M = 12;

export function defaultInputs(kit: StressKit): StressInputs {
  return Object.fromEntries(kit.inputs.map((s) => [s.key, s.default]));
}

// Standard normal CDF (Abramowitz–Stegun 7.1.26 erf; |error| < 1.5e-7).
function normCdf(x: number): number {
  const t = 1 / (1 + 0.3275911 * Math.abs(x) / Math.SQRT2);
  const poly = t * (0.254829592 + t * (-0.284496736 + t * (1.421413741
    + t * (-1.453152027 + t * 1.061405429))));
  const erf = 1 - poly * Math.exp(-(x * x) / 2);
  return 0.5 * (1 + (x >= 0 ? erf : -erf));
}

export function stressResponse(
  kit: StressKit, inputs: StressInputs = {}, phaseMonths = 6,
): StressResult {
  const x: StressInputs = { ...defaultInputs(kit), ...inputs };
  const pol = kit.policy;
  const sat = kit.satellites;
  const fv = kit.fair_value;

  // 1) policy reaction and curve
  const dPolicy = x.policy_bp + pol.policy_per_core_pp * x.core_pce_pp * 100;
  const d2y = pol.two_year_passthrough * dPolicy;
  const d10y = pol.ten_year_passthrough * dPolicy + x.term_premium_bp;
  const dSlope = d10y - d2y;
  const dU = x.unrate_pp + pol.unrate_per_100bp * dPolicy / 100;

  // 2) volatility and funding respond to the risk shock; sliders add on top
  const volMult = x.vol_mult * Math.exp((sat.rates_vol_d_vix ?? 0) * x.vix);
  const dFunding = x.funding_z + (sat.funding_d_vix ?? 0) * x.vix;

  // 2b) spreads and losses (satellites)
  const dBaa = sat.spread_d_vix * x.vix + sat.spread_d_u * dU + sat.spread_d_slope * dSlope / 100
    + (sat.spread_d_log_rvol ?? 0) * Math.log(volMult)
    + (sat.spread_d_funding_z ?? 0) * dFunding;
  const dHy = sat.hy_per_baa * dBaa;
  const dIg = sat.ig_per_baa * dBaa;
  const dCorp = sat.corp_loss_d_u * dU + sat.corp_loss_d_baa * dBaa
    + (sat.corp_loss_sloos_ci ?? 0) * x.sloos;
  const dCard = sat.consumer_loss_d_u * dU + sat.consumer_loss_sloos_card * x.sloos;

  const dEbitda = x.ebitda_pct + (sat.ebitda_d_u ?? 0) * dU;

  // 3) fair value moves too — only widening beyond it reads as cheap
  const dFvLog = fv.coef_logvix * Math.log(Math.max(fv.vix_now + x.vix, 1) / fv.vix_now)
    + fv.coef_slope * dSlope / 100 + fv.coef_rate * d10y / 100;
  // … and so does spread that pays for higher expected losses (unemployment)
  const dHyFv = fv.hy_fair_value * (Math.exp(dFvLog) - 1)
    + sat.hy_per_baa * sat.spread_d_u * dU;

  // 4) stance votes
  const ev = kit.stance.evidence;
  const th = kit.stance.thresholds;
  const votes: number[] = [];
  const labels = { valuation: 'unknown', loss_cycle: 'unknown', volatility: 'unknown', funding: 'unknown' };
  if (ev.hy_gap_pp != null) {
    const gap = ev.hy_gap_pp + dHy - dHyFv;
    const [l, v]: [string, number] = gap > th.gap_cheap_above_pp ? ['cheap', -1]
      : gap < th.gap_rich_below_pp ? ['rich', 1] : ['fair', 0];
    labels.valuation = l; votes.push(v);
  }
  if (ev.loss_chg_pp != null) {
    const chg = ev.loss_chg_pp + dCorp;
    const [l, v]: [string, number] = chg > th.loss_deteriorating_above_pp ? ['deteriorating', 1]
      : chg < th.loss_improving_below_pp ? ['improving', -1] : ['stable', 0];
    labels.loss_cycle = l; votes.push(v);
  }
  if (ev.rates_vol_bp != null && ev.rates_vol_uncond_bp) {
    const ratio = ev.rates_vol_bp * volMult / ev.rates_vol_uncond_bp;
    const [l, v]: [string, number] = ratio > th.vol_elevated_above_ratio ? ['elevated', 1]
      : ratio < th.vol_low_below_ratio ? ['low', -1] : ['normal', 0];
    labels.volatility = l; votes.push(v);
  }
  if (ev.funding_z != null) {
    const z = ev.funding_z + dFunding;
    const [l, v]: [string, number] = z >= th.funding_stressed_at_z ? ['stressed', 1]
      : z >= th.funding_elevated_at_z ? ['elevated', 1]
        : z >= th.funding_normal_at_z ? ['normal', 0] : ['calm', -1];
    labels.funding = l; votes.push(v);
  }
  const score = votes.reduce((a, b) => a + b, 0);
  const stance = votes.length === 0 ? 'unknown'
    : score >= th.risk_off_at_score ? 'risk_off'
      : score <= th.risk_on_at_score ? 'risk_on' : 'neutral';

  // 5) 12m total return
  const totalReturn: StressResult['total_return'] = {};
  const baseLoss = kit.total_return.base_loss_pp;
  for (const [tag, dSpread] of [['hy', dHy], ['ig', dIg]] as [string, number][]) {
    const t = kit.total_return.indices[tag];
    if (!t) continue;
    const ratePnl = -t.duration * (t.d_rate_base_pp + d10y / 100);
    const spreadPnl = -t.duration * (t.d_spread_base_pp + dSpread);
    const loss = (baseLoss + dCorp) * t.loss_mult;
    totalReturn[tag] = {
      total_return: t.carry + ratePnl + spreadPnl - loss,
      carry: t.carry, rate_pnl: ratePnl, spread_pnl: spreadPnl, expected_loss: loss,
    };
  }

  // 6) paths
  const P = kit.paths;
  const market = (h: number) => (phaseMonths > 0 ? Math.min(h / phaseMonths, 1) : 1);
  const lossW = (h: number) => Math.min(h / HORIZON_M, 1);
  const shifted = (base: number[] | null, delta: number, w: (h: number) => number) =>
    (base == null ? null : base.map((b, i) => b + delta * w(P.h[i])));
  // Fans keep their baseline proportional width around the shifted median.
  const fan = (f: Fan | null, d: number): Fan | null => {
    if (f == null) return null;
    const median = shifted(f.median, d, market)!.map((v) => Math.max(v, 0));
    const scaled = (q: number[]) => median.map((m, i) => (f.median[i] > 0 ? m * (q[i] / f.median[i]) : m));
    return { median, p16: scaled(f.p16), p84: scaled(f.p84) };
  };

  const paths: StressResult['paths'] = {
    h: P.h,
    policy: shifted(P.policy, dPolicy / 100, market),
    ust10: shifted(P.ust10, d10y / 100, market),
    corp_loss: shifted(P.corp_loss, dCorp, lossW),
    card_loss: shifted(P.card_loss, dCard, lossW),
    hy: fan(P.hy, dHy),
    ig: fan(P.ig, dIg),
  };
  // 10y band: ±1σ of rates vol, scaled by the scenario's vol multiple
  const rv = kit.stance.evidence.rates_vol_bp;
  if (paths.ust10 && rv != null) {
    const sd = P.h.map((h) => rv / 100 * Math.sqrt(h / HORIZON_M) * (1 + (volMult - 1) * market(h)));
    paths.ust10_band = {
      lo: paths.ust10.map((v, i) => v - sd[i]),
      hi: paths.ust10.map((v, i) => v + sd[i]),
    };
  }
  const fl = kit.floating;
  if (fl && P.sofr) {
    const sofr = shifted(P.sofr, dPolicy / 100, market)!;
    const coupon = sofr.map((s) => Math.max(s, fl.sofr_floor_pct) + fl.spread_pct);
    paths.sofr = sofr;
    paths.coupon = coupon;
    paths.share_icr_lt_1 = coupon.map((c, i) => {
      const h = P.h[i];
      const ebitda = (1 + fl.ebitda_growth_pct / 100) ** (h / 12)
        * Math.max(1 + dEbitda / 100 * market(h), 0.05);
      const zc = (Math.log(ebitda / (c / 100)) - Math.log(fl.leverage_median)) / fl.leverage_log_sd;
      return 1 - normCdf(zc);
    });
  }

  // 7) ABF collateral: drivers first, then losses on the total driver moves
  const abfOut: StressResult['abf'] = {};
  if (kit.abf) {
    const reg: Record<string, number> = { d_u: dU, d_vix: x.vix, sloos_card: x.sloos, sloos_ci: x.sloos };
    const extra: Record<string, number> = {};
    for (const [key, targets] of Object.entries(ABF_OVERLAYS)) {
      for (const t of targets) extra[t] = (extra[t] ?? 0) + (x[key] ?? 0);
    }
    const ordered = [...kit.abf.classes].sort(
      (a, b) => Number(a.kind !== 'driver') - Number(b.kind !== 'driver'));
    for (const c of ordered) {
      let d = c.usable
        ? Object.entries(c.coefs).reduce((acc, [r, b]) => acc + b * (reg[r] ?? 0), 0) : 0;
      if (c.kind === 'driver') {
        d += extra[c.target] ?? 0;
        reg[c.target] = d;
        abfOut[c.key] = { delta: d, stressed: null };
      } else {
        abfOut[c.key] = { delta: d, stressed: c.level == null ? null : Math.max(c.level + d, 0) };
      }
    }
  }

  return {
    abf: abfOut,
    deltas: {
      d_policy_bp: dPolicy, d_2y_bp: d2y, d_10y_bp: d10y, d_slope_bp: dSlope, d_unrate_pp: dU,
      d_baa_pp: dBaa, d_hy_oas_pp: dHy, d_ig_oas_pp: dIg, d_corp_loss_pp: dCorp,
      d_card_loss_pp: dCard, d_hy_fair_value_pp: dHyFv,
      vol_mult: volMult, d_funding_z: dFunding, d_ebitda_pct: dEbitda,
    },
    stance: { stance, score, ...labels },
    total_return: totalReturn,
    paths,
  };
}

// Recompute the preset scenarios and compare with macrobot's own answers.
export function verifyKit(kit: StressKit): { passed: number; total: number; failed: string[] } {
  const failed: string[] = [];
  for (const chk of kit.checks) {
    const got = stressResponse(kit, chk.inputs);
    const exp = chk.expected;
    let ok = got.stance.stance === exp.stance && Math.abs(got.stance.score - Number(exp.score)) < 1e-9;
    for (const tag of ['hy', 'ig']) {
      const want = exp[`${tag}_total_return`];
      if (want == null) continue;
      const have = got.total_return[tag]?.total_return;
      if (have == null || Math.abs(have - Number(want)) > 1e-6) ok = false;
    }
    for (const [key, want] of Object.entries(chk.expected_abf ?? {})) {
      const have = got.abf[key]?.delta;
      if (have == null || Math.abs(have - want) > 1e-6) ok = false;
    }
    if (!ok) failed.push(chk.name);
  }
  return { passed: kit.checks.length - failed.length, total: kit.checks.length, failed };
}
