# ABS New-Issue Spreads — Methodology

How the **ABS New-Issue Spread Tracker** chart constructs its spread numbers, with
the data sources, exact formulas, and known limits. Click the *methodology* link
in the panel header to land here.

The panel reads from `abs_new_issues` — a per-tranche table populated by parsing
SEC 424B5 prospectus supplements and (where available) the matching FWP pricing
term sheets. One tranche of an ABS deal is one row.

---

## What a "spread" means in this chart

For every tranche we store **one** number in `spread_to_benchmark`, in basis
points. Depending on how it was derived, the source is tagged in the
`spread_source` column:

| `spread_source` | Meaning | When it applies |
| --- | --- | --- |
| `parsed`  | The spread value was read directly from the offering document. | Floating-rate tranches (margin over SOFR is printed in the doc) and the small fraction of fixed-rate tranches where the prospectus prints an explicit spread-to-swaps column. |
| `implied` | The spread was *computed* from the tranche's coupon and the matched-tenor on-the-run Treasury yield on the filing date. | Fixed-rate tranches without a printed spread quote — the majority of US auto / equipment / credit-card ABS. |

The chart treats both equally on the y-axis. If you only want one kind, filter
the underlying API by `spread_source`.

---

## Implied-spread formula

For a fixed-rate tranche:

```
implied_spread_bps = (coupon_rate − UST_rate_at_matched_tenor_on_filing_date) × 100
```

- `coupon_rate` is the printed fixed coupon, in percent (e.g. 4.54 for 4.54%).
- `UST_rate_at_matched_tenor_on_filing_date` is the on-the-run constant-maturity
  Treasury yield on the day the 424B5 was filed, in percent. Source: FRED
  `DGS1` / `DGS2` / `DGS3` / `DGS5` / `DGS7` / `DGS10` (already cached daily in
  `metrics` table going back to 2015).
- Multiplier `× 100` converts the percent difference to basis points.

### Tenor mapping

WAL (weighted-average life of the tranche, years) → Treasury series:

| WAL range            | FRED series | Benchmark label |
| -------------------- | ----------- | --------------- |
| WAL < 1.5            | `DGS1`      | `UST1Y`         |
| 1.5 ≤ WAL < 2.5      | `DGS2`      | `UST2Y`         |
| 2.5 ≤ WAL < 3.5      | `DGS3`      | `UST3Y`         |
| 3.5 ≤ WAL < 6.0      | `DGS5`      | `UST5Y`         |
| 6.0 ≤ WAL < 8.5      | `DGS7`      | `UST7Y`         |
| WAL ≥ 8.5            | `DGS10`     | `UST10Y`        |

The half-point boundaries pick the *closer* on-the-run tenor (no interpolation).
The 10-year benchmark catches everything beyond 8.5 years; 20/30Y tenors aren't
tracked because almost no offered ABS class has a WAL that long.

### Worked example

> Toyota Auto Receivables 2025-A Owner Trust, Class A-3 Notes,
> filed 2025-01-16, coupon 4.78% fixed, WAL 2.42 years.
> WAL is in the 1.5–2.5 bucket → `DGS2` on 2025-01-16 = 4.27%.
>
> `implied_spread_bps = (4.78 − 4.27) × 100 = 51 bps`

---

## Why UST and not SOFR swaps

Industry dealers more commonly quote new-issue ABS spreads against the
interpolated swaps curve (often labelled `I-CRV` or `SWAPS`) than against the
on-the-run Treasury. We use Treasuries because:

1. **The data is in the dashboard.** FRED publishes the constant-maturity
   Treasury yields daily (`DGS*` series) with no key or scrape. SOFR swap rates
   at WAL tenors are not freely available from FRED.
2. **The movements are equivalent.** Swap spreads (SOFR swap minus matching UST)
   are small and slowly changing — typically 5–15 bps for 1–3 year tenors and
   moving by single bps week to week. For tracking *changes* in new-issue
   spread, "to-UST" and "to-swaps" curves move together within a near-constant
   offset.
3. **Floating spreads are already to SOFR.** Floating-rate tranches in the
   chart carry their printed SOFR margin, not a synthetic UST-equivalent. The
   chart simply shows both UST-based and SOFR-based numbers on the same axis
   — that's the industry convention.

A more accurate spread for fixed tranches would be to the matched-tenor
interpolated SOFR swap. If you wire in a SOFR-swap curve source (e.g. ICE Swap
Rate or Fed Reserve H.15 USD-IRS), swapping the lookup in
`backend/data/abs_parser.py::_get_treasury_rate_on_date` would make the change
narrow and contained.

---

## Where ratings come from

The chart's rating-bucket filter (AAA / AA / A / BBB / BB-and-below / all) needs
each row to carry at least one agency rating. Reality of these filings:

- **424B5 prospectus supplements rarely disclose ratings inline.** Across a
  cross-section of asset classes we checked (auto, credit card, equipment,
  aircraft, etc.), only a small minority of 424B5s embed the agency ratings in
  the offering document itself.
- **Ratings appear in the matching FWP pricing term sheets** filed by the same
  trust within a few days of the 424B5.

We backfill ratings into `abs_new_issues` from the `abs_pricing` table (which
parses the FWP filings) using a one-time join:

- Match on cleaned trust name + canonical class label (strip "Class" / "Notes"
  wrappers, drop non-alphanumerics).
- Require the FWP filing date within 30 days of the 424B5 filing date.
- `abs_pricing.rating` is a combined "Moody's/S&P" string (e.g. `Aaa/AAA`). The
  join script splits it into `rating_moodys` and `rating_sp` so the chart's
  `_RATING_BUCKET_MAP` finds a match.

Script: `scripts/join_abs_pricing_to_new_issues.py`. Idempotent — re-running
only fills NULLs.

---

## Coverage limits

| Field                  | Coverage path                                  | Realistic ceiling |
| ---------------------- | ---------------------------------------------- | ----------------- |
| `coupon_rate`          | Structured pandas parse, Claude fallback       | ~75% of rows      |
| `wal_years`            | Structured parse + FWP-join + reparse          | ~50% of rows      |
| `spread_to_benchmark`  | Direct parse + implied (needs coupon + WAL)    | depends on WAL    |
| `rating_*`             | FWP-join only — 424B5s rarely disclose         | sparse            |
| `floating_spread_bps`  | Structured parse (margin is on cover)          | ~all floating     |

Why so many gaps:

- **Master-trust deals (credit cards, dealer floorplan) often have no WAL.**
  These are revolving structures — the tranche has no contractual amortization
  schedule, so a single WAL number isn't reported.
- **Retained tranches** (e.g. "Collateral Interest", subordinate slices
  not offered to the public) appear as rows but carry no public economics.
- **Some 424B5s are amendments / supplements** that reference the original
  filing without restating tranche detail.

The chart filters out `parse_confidence = 'low'` rows by default to keep noise
down, and points where `spread_to_benchmark` is NULL are simply skipped.

---

## Reading the chart

- Each point is a weekly bin: median (line), min/max (band) of all qualifying
  tranches in that week for the selected asset class + rating bucket.
- A widening trend = the market is demanding more spread for the same risk
  — typically reads as either (a) tightening credit conditions, (b) a forward
  re-rating of consumer credit risk, or (c) idiosyncratic deal supply pressure.
- A tightening trend = either rate-cut-cycle compression, abundant liquidity,
  or strong appetite for the asset class.
- Compare against the macro tab's HY OAS and the BDC mark-to-cost trend — auto
  ABS senior spreads tend to widen *after* HY OAS spikes and *before* BDC
  marks decline, making them a useful intermediate indicator.

---

## Code map

- `backend/data/abs_parser.py` — structured pandas + Claude-fallback parser for
  424B5s; computes `spread_to_benchmark` for fixed-rate tranches via
  `_compute_spread`.
- `backend/data/abs_reparse.py` — re-extraction pass over stored rows (cached
  HTML at `backend/cache/store/abs_424b5/`). `apply_implied_spread()` recomputes
  spreads after WAL is filled in by other paths.
- `backend/data/abs_pricing.py` — FWP pricing-term-sheet parser; populates
  `abs_pricing`.
- `scripts/backfill_abs_parser.py` — initial 3-year backfill of `abs_new_issues`.
- `scripts/backfill_abs_pricing.py` — initial 3-year backfill of `abs_pricing`.
- `scripts/join_abs_pricing_to_new_issues.py` — one-time rating + WAL join from
  `abs_pricing` into `abs_new_issues`, then runs `apply_implied_spread`.
- `backend/api/routes.py::get_abs_spread_series` — the chart's data source;
  rating-bucket map lives there.
