# Macro / Credit Leading Indicators — Research & Build Notes

A consolidated reference for the higher-level, predictive metrics layered on top of
the raw FRED/market feeds. Each entry gives the construction, the exact public data
source, update cadence, documented predictive value, and caveats. Verified May 2026.

**Legend** — Build status of each indicator in this repo:
- ✅ **live** — wired in (`data/indicators.py` or `config/data_sources.yaml`), pulling now
- 🔧 **roadmap** — researched and specced below, not yet wired
- ⚠️ **correction** — fixes a wrong series ID / formula / framing from the original notes

---

## Tier 1 — Academically validated, free via FRED / Fed

### 1. NY Fed Recession Probability (Estrella-Mishkin probit) ✅
- **What / lead:** P(NBER recession within 12 months) from the slope of the yield curve. The single most-cited one-variable recession model.
- **Formula:** `P = Φ(α + β · spread)`, spread = 10y−3m term spread (pp). We use the canonical **Estrella & Mishkin (1996)** coefficients **α = −0.5333, β = −0.6629**, applied to the **monthly average** of the daily spread.
- **Data:** FRED **`T10Y3M`** (the 10y−3m spread is more reliable than 2s10s here). The NY Fed's live model is re-estimated over 1959–2009 and published as [`allmonth.xls`](https://www.newyorkfed.org/medialibrary/media/research/capital_markets/allmonth.xls); the classic coefficients reproduce it closely without needing `.xls` parsing.
- **Cadence:** `T10Y3M` daily; we recompute monthly points on every FRED job.
- **Build:** `compute_nyfed_recession_probit()` → series **`NYFED_RECESSION_PROB`** (percent, to share an axis with FRED's `RECPROUSM156N`).
- **Caveats:** The 2022–23 inversion drove model probabilities very high without (so far) an NBER recession — QE/term-premium compression may have degraded the single-variable signal. Cross-check against the EBP probit (#11).
- **Cite:** Estrella & Mishkin (1996), NY Fed *Current Issues* 2(7); [NY Fed Yield Curve FAQ](https://www.newyorkfed.org/research/capital_markets/ycfaq).

### 2. Excess Bond Premium — Gilchrist-Zakrajšek ✅
- **What / lead:** The component of corporate credit spreads *not* explained by expected default — a measure of bond-market risk appetite. The predictive power of credit spreads for downturns is due **entirely** to the EBP, not the raw spread level.
- **Data:** Fed FEDS Note CSV — [`ebp_csv.csv`](https://www.federalreserve.gov/econres/notes/feds-notes/ebp_csv.csv). Columns: `date, gz_spread, ebp, est_prob`. Coverage 1973→present. Confirmed latest (2026-04): ebp=−0.219, gz_spread=0.916, est_prob=0.135.
- **Cadence:** Monthly, after 10:00 a.m. on the **4th business day**; same URL overwritten in place.
- **Build:** `fetch_excess_bond_premium()` → **`EBP`**, **`GZ_SPREAD`** (raw units), **`EBP_REC_PROB`** (percent).
- **Caveats:** Staff research product, not an official release — subject to revision. Negative EBP (current) = compressed risk premia, not itself a recession signal.
- **Cite:** Gilchrist & Zakrajšek (2012) *AER* 102(4); [Favara, Gilchrist, Lewis & Zakrajšek FEDS Note (2016)](https://www.federalreserve.gov/econres/notes/feds-notes/recession-risk-and-the-excess-bond-premium-20160408.html).

### 3. Sahm Rule ✅
- **What / lead:** Triggers when the 3-month avg unemployment rate is **≥ 0.50 pp** above its prior-12-month low. More a recession *confirmer* (fires ~2–4 months after onset) than a leading indicator.
- **Data:** FRED **`SAHMREALTIME`** (real-time unemployment vintages — honest for backtests) and **`SAHMCURRENT`** (latest-revised data). Both currently ~0.13, well below trigger.
- **Build:** config (pure FRED).
- **Caveats:** Fired falsely in **July 2024** — post-pandemic labor-supply growth inflated unemployment for non-demand reasons. Pair with the SF Fed Labor Market Stress Indicator (#8), whose state-breadth did *not* confirm.
- **Cite:** Sahm (2019), Hamilton Project; [SF Fed, "Tracking Labor Market Stress" (2025)](https://www.frbsf.org/research-and-insights/publications/economic-letter/2025/08/tracking-labor-market-stress/).

### 4. SLOOS Net Tightening ✅ ⚠️
- **What / lead:** Net % of banks tightening standards. Lagged **4 quarters**, it leads the consumer delinquency curves — exactly the curves tracked in the deal dashboards.
- **⚠️ Series-ID corrections to the original notes:**
  - C&I → **`DRTSCILM`** (large/middle-market). `DRTSCLNC` is **not a valid ID**.
  - Credit card → **`DRTSCLCC`** (1996→present). `STDSCREDIT` **does not exist**.
  - Auto → **`STDSAUTO`** (valid, but only starts 2011-Q2).
- **Data:** FRED, quarterly, ~2 weeks after the relevant FOMC meeting.
- **Build:** config (`DRTSCILM` already present; added `DRTSCLCC`, `STDSAUTO`).
- **Underlying model:** Fed's [Predicting Credit Card Delinquency Rates (2025)](https://www.federalreserve.gov/econres/notes/feds-notes/predicting-credit-card-delinquency-rates-20250228.html) — OLS on prime rate, unemployment, real revolving credit, **SLOOS card tightening lagged 4Q (+0.64\*)**, nonprime balance share lagged 4Q. Adj. R² ≈ 0.97. The 4Q lag separates the causal effect of tightening from banks tightening *in reaction to* deterioration.

### 5. Household Debt Service Ratio ✅ ⚠️
- **What / lead:** Required debt payments as a share of disposable income; rising DSR mechanically constrains consumption and precedes delinquency.
- **Data:** FRED **`TDSP`** (total), **`CDSP`** (consumer ex-mortgage — watch this one), **`MDSP`** (mortgage). Quarterly, ~1-quarter+ lag.
- **⚠️ Methodology break:** From **2024-Q2**, the Board switched to a **credit-bureau (FRBNY/Equifax CCP) microdata** method that sums actual scheduled per-tradeline payments, *replacing* the old imputed series. Pre/post-2024Q2 vintages are not constructed identically — flag the splice; don't trend across it. `MDSP` is distorted by the locked-in sub-3% mortgage stock, so `CDSP` is the cleaner consumer-stress read.
- **Build:** config.
- **Cite:** [Introducing a Credit Bureau-Based Measure of U.S. Household Debt Service (2024)](https://www.federalreserve.gov/econres/notes/feds-notes/introducing-a-credit-bureau-based-measure-of-u-s-household-debt-service-20240904.html).

---

## Tier 2 — High practitioner value, partially free

### 6. Credit Impulse ✅ ⚠️
- **What / lead:** Change in the *flow* of new credit, scaled by GDP — the second derivative of the credit stock. Leads **GDP / real-economy momentum by ~9–12 months** (the well-documented claim). The "leads S&P 500 EPS by ~9–10 months" version is a sell-side heuristic, not peer-reviewed — treat as directional.
- **Formula:** `Δ(ΔCreditStock) / GDP`. ⚠️ The user's `TOTALSL` is a **consumer-only** proxy (no business/mortgage credit). For the economy-wide version use **`TCMDO`** (total nonfinancial debt); subtract government for a private-credit version.
- **Data:** FRED `TCMDO`, `BUSLOANS`, `TOTALSL`, `GDP`. Quarterly; Z.1 source ~10–11 weeks after quarter-end.
- **Caveats:** A second derivative — noisy; smooth with 6-month windows. Misleading during QE (idle liquidity not captured).
- **Cite:** Biggs/Mayer/Pick (Deutsche Bank, 2008); [ARP, "The Credit Impulse Explained"](https://www.arpinvestments.com/insights/the-credit-impulse-explained).
- **Build:** `compute_credit_impulse()` → series **`CREDIT_IMPULSE`** from `TCMDO`/`GDP`. Construction used: YoY change in the annual credit flow over nominal GDP — `[(C_t−C_{t−4})−(C_{t−4}−C_{t−8})]/GDP_t × 100` (the 1-year differencing damps the second-difference noise). Note the `TCMDO` ($mn) / `GDP` ($bn) unit conversion.

### 7. NY Fed Household Debt & Credit — Delinquency Transition Rates ✅
- **What / lead:** Share of balances *transitioning into* 30+/90+ delinquency by loan type — the **flow** version of the FRED charge-off stocks; flows lead stocks.
- **Data:** Excel workbook, pattern `https://www.newyorkfed.org/medialibrary/interactives/householdcredit/data/xls/hhd_c_report_{YYYYQ#}.xlsx`. The flow data lives on **Page 13 Data** (30+) and **Page 14 Data** (90+), columns AUTO/CC/MORTGAGE/HELOC/STUDENT/OTHER/Total, rows keyed `YY:Qn`. Server 403s bare fetchers — needs a browser UA.
- **Cadence:** Quarterly, ~6 weeks after quarter-end.
- **Build:** `data/hhdc.py::fetch_hhdc_transitions()` probes recent quarters for the newest workbook, parses both flow pages, and stores 14 series **`HHDC_FLOW{30,90}_{AUTO,CC,MORTGAGE,HELOC,STUDENT,OTHER,ALL}`** (category `delinquency_flow`, 2003Q1→present). Daily scheduler job + `POST /api/hhdc/refresh`. Requires `openpyxl`.

### 8. SF Fed Labor Market Stress Indicator (LMSI) 🔧 ⚠️
- **What / lead:** Solves the Sahm Rule's false-positive problem via **breadth across states**.
- **⚠️ Construction:** NOT a claims-to-labor-force ratio. It **counts states with "accelerating unemployment"** (a state-level Sahm rule: state UR ≥ 0.5 pp above its 12-month low). US has been in recession every time ≥ ~30 states qualified; the weekly version uses a Sahm-equivalent threshold near 0.2.
- **Data:** [frbsf.org LMSI](https://www.frbsf.org/research-and-insights/data-and-indicators/labor-market-stress-indicator/); downloadable [`.xlsx`](https://www.frbsf.org/wp-content/uploads/labor-market-stress-indicator-data.xlsx). No API. Weekly.
- **Cite:** Garimella, Jordà & Singh (2025), [SF Fed WP 2025-31](https://www.frbsf.org/wp-content/uploads/wp2025-31.pdf).

### 9. ABS New-Issue Spread Momentum ✅
- **What / lead:** New-issue spreads on subprime/prime auto and equipment ABS as a forward read on consumer risk premium — widening *subordinate subprime auto* tranches flag stress before realized delinquencies. **No FRED series**; deal-level only.
- **Data:** the underwriter **pricing term sheet**, filed as an FWP on pricing day, carries a tranche table with a spread column (over interpolated swaps / SOFR). Ratings FWPs (filed earlier) have no spread column — the presence of a parseable spread table cleanly distinguishes the pricing sheet. The pricing sheet is reached via the deal **trust's** filing history (it doesn't name the deal, so it isn't directly text-searchable).
- **Build:** `data/abs_pricing.py` — full-text-searches EDGAR for ABS FWPs, discovers trust CIKs, enumerates each trust's FWPs, parses tranche tables with `pandas.read_html` (needs `lxml`), and stores per-tranche spreads in the `abs_pricing` table (segment-tagged prime/subprime/equipment). Token-free regex parse; shares the EDGAR job cadence. API: `GET /api/abs/pricing`, `GET /api/abs/spread-momentum`, `POST /api/abs/pricing/refresh`. Frontend: ABS-page "New-Issue Spreads" panel. *Validated: Carvana (subprime) C+155/D+190 vs CarMax (prime) C+95/D+130 — the risk-premium gap.*
- **Note:** also fixed a latent bug in the existing `edgar.py` — EDGAR FTS now 500s on `dateRange=custom`/`_source` params, so the ABS-filing monitor was silently returning nothing.

---

## Tier 3 — Constructible composites

### 10. Consumer Financial Stress Index (CFSI) ✅
- **What:** A single dashboard needle — equal-weight average of the z-scores of four quarterly consumer-stress inputs, each oriented so higher = more stress: `CDSP` (Consumer DSR) + `DRTSCLCC` SLOOS card tightening **lagged 4Q** + `HHDC_FLOW30_ALL` flow into 30+ delinquency (#7) + real revolving-credit growth (`REVOLSL`/`CPIAUCSL` YoY).
- **Build:** `compute_cfsi()` → series **`CFSI`** (in σ; 0 = post-2015 avg). Runs in the FRED job tail and is recomputed at the end of the HHDC job so new transition data flows in immediately.
- **Caveats:** z-scores normalized over the available window (~2016→present, bounded by the 2015 FRED fetch start), so the baseline excludes the GFC — read it as "stress vs. the last decade," not vs. all history. The frontier quarter is bound by the Consumer DSR's ~1–2 quarter publication lag. Note the missing Oct-2025 CPI (BLS shutdown) drops that one quarter's real-credit deflator.

### 11. NY Fed Recession Probability Enhanced (EBP + term spread) ✅
- **What / lead:** Probit on term spread + EBP (+ real funds rate). EBP carries most of the signal: **+50 bps EBP → +15 pp** recession prob vs. **−50 bps term spread → ~4 pp** — EBP ~4× more powerful at the 12-month horizon. Outperforms yield-curve-only.
- **Data:** Already provided as `est_prob` in the EBP CSV — **no separate regression needed**. Stored as **`EBP_REC_PROB`** (see #2). (Note: it's a single-equation probit with two key predictors, not literally a "bivariate probit.")
- **Cite:** [Updating the Recession Risk and the Excess Bond Premium (2016)](https://www.federalreserve.gov/econres/notes/feds-notes/updating-the-recession-risk-and-the-excess-bond-premium-20161006.html).

---

## Expansion — additional validated public indicators

Discovered while going wide. Prioritized; all free.

### Yield curve
- **★ Near-Term Forward Spread (NTFS)** ✅ — Engstrom-Sharpe; the Fed's preferred recession-curve measure (18m-fwd 3m minus current 3m). A 1-SD (~80bp) drop adds ~35pp to 12-month recession prob; **dominates 2s10s** once included. **Built** via `compute_near_term_forward_spread()` → series **`NEAR_TERM_FWD_SPREAD`**, reconstructed from the Fed Board GSW Svensson params ([feds200628.csv](https://www.federalreserve.gov/data/yield-curve-tables/feds200628.csv)) — fully public, no sheet-scraping. Captures near-term policy expectations, a different concept from our term-spread probit.

### Financial conditions / systemic risk
- **★ Adjusted NFCI (`ANFCI`)** ✅ — financial conditions *relative to the economy* (the leading variant of NFCI). Weekly. **Live.**
- **OFR Financial Stress Index** ✅ — daily, 33 global variables; documented to Granger-lead the CFNAI. **Built** via `fetch_ofr_fsi()` → `OFR_FSI` headline + 5 category contributions (`OFR_FSI_{CREDIT,EQUITY,SAFE,FUNDING,VOL}`), from the OFR [CSV](https://www.financialresearch.gov/financial-stress-index/data/fsi.csv) (back to 2000). Frontend: Macro-page "OFR Financial Stress Index" panel. Not on FRED.
- **`STLFSI4`** ✅ (already configured) — SOFR-based; already embeds the modern TED-spread successor.
- **`NFCI`** ✅ (already configured); sub-indices `NFCICREDIT`/`NFCILEVERAGE`/`NFCIRISK` 🔧 available.

### Liquidity / funding stress 🔧
- Commercial paper spread: `RIFSPPNA2P2D30NB` (A2/P2 30d) minus `CPN3M` (AA), or `CPFF`. Daily.
- SOFR-based funding stress: `SOFR90DAYAVG` − `DTB3`. Plumbing: `WRESBAL` (reserves), `RRPONTSYD` (RRP).

### Leading composites
- **`CFNAI` / `CFNAIMA3`** ✅ (already configured) — actionable rule: **CFNAI-MA3 < −0.70** after an expansion signals rising recession risk. Free LEI substitute.
- **★ Weekly Economic Index (`WEI`)** ✅ — Lewis-Mertens-Stock weekly GDP-equivalent nowcast. **Live.** (Coincident/nowcast, not a 6–12mo lead.)
- **ADS Business Conditions Index** 🔧 — Philly Fed, ~8×/month. *Coincident, not leading* — for confirmation only. Not on FRED.

### Labor leads
- **★ Temp Help Employment (`TEMPHELPS`)** ✅ — firms cut temps before permanent staff; classic early-cycle roll-over. Interpret YoY (secular downtrend). **Live.**
- **★ JOLTS Quits Rate (`JTSQUR`)** ✅ — worker-confidence read; falling quits leads labor deterioration. **Live.** Companions: `JTSJOL` (openings), `JTSLDR` (layoffs); V/U = `JTSJOL`/`UNEMPLOY`.
- **Continuing claims (`CCSA`)** 🔧 — trough leads recession starts by ~3–20 months (avg ~11). Plus `IC4WSA`.
- **Avg weekly hours mfg (`AWHMAN`)** 🔧 — hours cut before headcount; LEI component.

### Credit / consumer / money 🔧
- **Real M2 (`M2REAL`)** — YoY contraction is rare and historically tied to severe stress (use as confirmation; velocity instability post-2008).
- C&I / consumer credit growth: `BUSLOANS`, `REVOLSL` (volumes follow SLOOS standards).

### Housing
- **★ Building Permits (`PERMIT`)** ✅ — one of the strongest single recession predictors; LEI component; rate-sensitive. **Live.** Single-family `PERMIT1` 🔧.
- **NAHB HMI** 🔧 — builder confidence leads permits 1–3 months (redistribution restricted; check FRED availability).

### Inflation regime
- **`T5YIFR`** ✅, **`T5YIE`** ✅ (already configured). **Cleveland Fed expectations** (`EXPINF1YR`/`5YR`/`10YR`) 🔧 — cleaner than raw breakevens.

### Corporate stress
- **`BAMLH0A0HYM2`** ✅ (HY OAS, configured). Add **CCC-minus-BB** (`BAMLH0A3HYC` − BB) as a distress-concentration gauge 🔧. Prefer rate-of-change over absolute thresholds (QE-distorted).

### Long-horizon systemic (years ahead)
- **★ BIS Credit-to-GDP Gap (Borio-Drehmann)** ✅ — best early-warning indicator at 2–5yr horizons; basis for Basel III countercyclical buffers. `(Credit/GDP) − one-sided HP trend (λ=400k)`; >~10pp = elevated. **Built** via `fetch_bis_credit_gap()` → `BIS_CREDIT_GAP_US`, ingesting BIS's *pre-computed* gap (series `Q:US:P:A:C`) from the [data-portal bulk CSV](https://data.bis.org/static/bulk/WS_CREDIT_GAP_csv_col.zip), back to 1957. Currently **−12.1pp** (US credit well below trend — the post-GFC deleveraging signature, no boom risk). Frontend: Macro-page "BIS Credit-to-GDP Gap" panel. Signals "too early"; HP end-point instability — a slow vulnerability gauge, not a timer. Pairs with Credit Impulse (#6).
- **BIS total private DSR** 🔧 — dominates the credit gap at 1–2yr horizons for crisis prediction.

---

## What's wired now (this build)

**Computed / ingested** (`backend/data/indicators.py`, run at the tail of the FRED job; also `POST /api/indicators/refresh`):
`NYFED_RECESSION_PROB`, `EBP`, `GZ_SPREAD`, `EBP_REC_PROB`, `NEAR_TERM_FWD_SPREAD` (1980→), `CREDIT_IMPULSE`, `CFSI`, `OFR_FSI` (+5 categories), `BIS_CREDIT_GAP_US`, `NTFS_REC_PROB`, `RECESSION_RISK_ENSEMBLE`, `USREC`.

**Recession-risk ensemble** (the headline gauge): equal-weight mean of three 12-mo recession-probability models — the NY Fed yield-curve probit, the EBP probit, and a Near-Term Forward Spread logit *fit here* against forward-12-month NBER recessions (`USREC`) over 1980–present (≈5 cycles, ~557 obs). Falls back to the two published probits if NTFS can't be fit. Equal-weight by design — robust and transparent. Stored as `RECESSION_RISK_ENSEMBLE`; latest ≈19% (vs ~42% entering 2008, ~46% at the 2022–23 inversion that didn't recess — the curve-model caveat). Frontend: the Macro-page "Recession Risk — Ensemble" headline panel (ensemble bold over its three components).

**NY Fed HHDC ingest** (`backend/data/hhdc.py`, daily job + `POST /api/hhdc/refresh`):
14 `HHDC_FLOW{30,90}_{loan}` transition-rate series.

**ABS new-issue spread tracker** (`backend/data/abs_pricing.py`, shares the EDGAR job + `POST /api/abs/pricing/refresh`):
per-tranche spreads in the `abs_pricing` table, segment-tagged. `GET /api/abs/pricing`, `GET /api/abs/spread-momentum`.

**New FRED series** (`config/data_sources.yaml`):
`T10Y3M`, `SAHMREALTIME`, `SAHMCURRENT`, `DRTSCLCC`, `STDSAUTO`, `TDSP`, `CDSP`, `MDSP`, `ANFCI`, `WEI`, `TEMPHELPS`, `JTSQUR`, `PERMIT`, `TCMDO`, `GDP`.

All surface through the existing `GET /api/fred/latest` and `GET /api/fred/history/{series_id}` endpoints (categories `recession_risk`, `delinquency_flow`, `credit`).

**Frontend** (`MacroPage` → `DashboardPanels.tsx`): Recession Probability panel overlays all three 12-mo probits; new Consumer Stress (CFSI), Sahm Rule, Near-Term Forward Spread, Credit Impulse, and Flow-into-Delinquency panels.

## Suggested next builds (in order)
1. **CFSI v2** — extend the component history pre-2015 (longer FRED fetch window) so the z-score baseline spans the GFC; consider a PCA weighting instead of equal-weight.
2. **ABS spread momentum v2** — a spread-vs-prior-quarter delta per segment/seniority (a true momentum z-score), and widen coverage to credit-card/student/equipment shelves. The `get_abs_spread_momentum()` series is the starting point.
3. **Ensemble v2** — recession-probability-weighted (not equal-weight) blend; add recession shading (`USREC`) to the chart; consider an optimal-weight or model-confidence weighting.
