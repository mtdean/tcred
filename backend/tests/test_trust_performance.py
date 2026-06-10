"""
Tests for data/trust_performance.py — 10-D master-trust performance parser.

Parser fixtures are synthetic but mirror the real label/layout variants seen
in the May-2026 filings of each trust family:
  * BA      — headline "60+-Day Delinquency Rate", "Net Charge-Offs as a
              percentage of Average Principal Receivables"
  * Chase   — "Net Losses as a percentage of Average Pool Balance", bucket
              table with per-bucket percentages
  * Citi    — dollar-amount buckets + "Current" balance, "Credit Loss
              Component" as the only loss-rate disclosure
  * WFN     — "60+ days delinquent" summary row alongside granular buckets
  * WF      — "30 to 59 Days Delinquent" with accounts-% before receivables-%
"""

from __future__ import annotations

import pytest

from cache import db
from data import trust_performance as tp


def _html(body: str) -> str:
    return f"<html><body>{body}</body></html>"


def _cells(*vals) -> str:
    return "".join(f"<td>{v}</td>" for v in vals)


# ─── parse_trust_metrics: direct labels ──────────────────────────────────────
class TestDirectLabels:
    def test_ba_style_headline_labels(self):
        html = _html(
            "<p>(b) 60+-Day Delinquency Rate</p><p>0.96%</p>"
            "<p>Total Charge-Offs as a percentage of Average Principal Receivables Outstanding</p><p>2.99</p><p>%</p>"
            "<p>Net Charge-Offs as a percentage of Average Principal Receivables Outstanding</p><p>2.39</p><p>%</p>"
            "<p>Collections as a percentage of prior month Principal Receivables</p><p>27.42%</p>"
            "<p>Portfolio Yield</p><p>15.92%</p>"
            "<p>Base Rate</p><p>4.38%</p>"
        )
        out = tp.parse_trust_metrics(html)
        assert out == {
            "delinq_60plus_rate": 0.96,
            "gross_charge_off_rate": 2.99,
            "net_charge_off_rate": 2.39,
            "payment_rate": 27.42,
            "portfolio_yield": 15.92,
            "base_rate": 4.38,
        }

    def test_spot_value_preferred_over_three_month_average(self):
        html = _html(
            "<p>Three-Month Average 60+-Day Delinquency Rate</p><p>0.98%</p>"
            "<p>60+-Day Delinquency Rate</p><p>0.96%</p>"
        )
        assert tp.parse_trust_metrics(html)["delinq_60plus_rate"] == 0.96

    def test_three_month_average_used_when_no_spot(self):
        html = _html("<p>Three-Month Average 60+-Day Delinquency Rate</p><p>0.98%</p>")
        assert tp.parse_trust_metrics(html)["delinq_60plus_rate"] == 0.98

    def test_gross_portfolio_yield_skipped(self):
        # WFN reports Gross Portfolio Yield before the net figure; the net one
        # is the standard metric.
        html = _html(
            "<p>Gross Portfolio Yield (current month)</p><p>39.00%</p>"
            "<p>Portfolio Yield (current month)</p><p>13.31%</p>"
        )
        assert tp.parse_trust_metrics(html)["portfolio_yield"] == 13.31

    def test_value_without_percent_sign_rejected(self):
        # Dollar amounts must not leak in as rates.
        html = _html("<p>Net Charge-Off Rate</p><p>$28,201</p>")
        assert "net_charge_off_rate" not in tp.parse_trust_metrics(html)

    def test_citi_credit_loss_component_fallback(self):
        html = _html("<p>Credit Loss Component</p><p>2.11 %</p>")
        assert tp.parse_trust_metrics(html)["net_charge_off_rate"] == 2.11


# ─── parse_trust_metrics: delinquency bucket tables ──────────────────────────
class TestBucketTables:
    def test_percent_buckets_chase_style(self):
        rows = (
            f"<tr>{_cells('30-59 days', '4,646', '28,899,331.96', '0.24%')}</tr>"
            f"<tr>{_cells('60-89 days', '3,080', '22,955,167.05', '0.19%')}</tr>"
            f"<tr>{_cells('90-119 days', '2,500', '18,000,000.00', '0.15%')}</tr>"
            f"<tr>{_cells('120-149 days', '2,000', '15,000,000.00', '0.12%')}</tr>"
            f"<tr>{_cells('150-179 days', '1,800', '13,000,000.00', '0.10%')}</tr>"
            f"<tr>{_cells('180+ days', '900', '8,000,000.00', '0.07%')}</tr>"
        )
        out = tp.parse_trust_metrics(_html(f"<table>{rows}</table>"))
        assert out["delinq_30plus_rate"] == 0.87
        assert out["delinq_60plus_rate"] == 0.63
        assert out["delinq_90plus_rate"] == 0.44

    def test_dollar_buckets_citi_style(self):
        body = (
            "<p>6. Delinquency</p>"
            f"<p>Current</p><p>$</p><p>800,000,000</p>"
            f"<p>1-30 days delinquent</p><p>$</p><p>100,000,000</p>"
            f"<p>31-60 days delinquent</p><p>$</p><p>60,000,000</p>"
            f"<p>61-90 days delinquent</p><p>$</p><p>25,000,000</p>"
            f"<p>91-120 days delinquent</p><p>$</p><p>15,000,000</p>"
        )
        out = tp.parse_trust_metrics(_html(body))
        # total = 1,000,000,000; 30+ = 100M, 60+ = 40M, 90+ = 15M
        assert out["delinq_30plus_rate"] == 10.0
        assert out["delinq_60plus_rate"] == 4.0
        assert out["delinq_90plus_rate"] == 1.5

    def test_open_ended_summary_row_dropped(self):
        # WFN prints a "60+ days delinquent" total next to granular buckets —
        # summing it would double-count.
        rows = (
            f"<tr>{_cells('31-60 days delinquent', '$66,000,000', '1.33%')}</tr>"
            f"<tr>{_cells('61-90 days delinquent', '$54,000,000', '1.09%')}</tr>"
            f"<tr>{_cells('91-120 days delinquent', '$47,000,000', '0.96%')}</tr>"
            f"<tr>{_cells('60+ days delinquent', '$101,000,000', '2.05%')}</tr>"
        )
        out = tp.parse_trust_metrics(_html(f"<table>{rows}</table>"))
        assert out["delinq_30plus_rate"] == pytest.approx(3.38)
        assert out["delinq_60plus_rate"] == pytest.approx(2.05)

    def test_dual_percentage_rows_prefer_receivables_pct(self):
        # WF prints % of accounts first, % of receivables after the $ amount.
        rows = (
            f"<tr>{_cells('30 to 59 Days Delinquent', '4,537', '0.14%', '$37,024,531.53', '0.40%')}</tr>"
            f"<tr>{_cells('60 to 89 Days Delinquent', '3,509', '0.11%', '$32,507,680.11', '0.35%')}</tr>"
            f"<tr>{_cells('90 to 119 Days Delinquent', '3,000', '0.10%', '$28,476,777.31', '0.31%')}</tr>"
        )
        out = tp.parse_trust_metrics(_html(f"<table>{rows}</table>"))
        assert out["delinq_30plus_rate"] == pytest.approx(1.06)
        assert out["delinq_90plus_rate"] == pytest.approx(0.31)

    def test_non_monotonic_bucket_values_discarded(self):
        # BA's "Delinquency Experience" table is a multi-year history; if the
        # derived rates come out non-monotonic, they're garbage — keep only
        # the direct-label value.
        html = _html(
            "<p>60+-Day Delinquency Rate</p><p>0.96%</p>"
            "<table>"
            f"<tr>{_cells('30-59 days', '2.64%')}</tr>"
            f"<tr>{_cells('90-119 days', '2.00%')}</tr>"
            "</table>"
        )
        out = tp.parse_trust_metrics(html)
        assert out["delinq_60plus_rate"] == 0.96
        assert "delinq_30plus_rate" not in out
        assert "delinq_90plus_rate" not in out

    def test_direct_label_wins_over_bucket_value(self):
        html = _html(
            "<p>30+-Day Delinquency Rate</p><p>5.00%</p>"
            "<table>"
            f"<tr>{_cells('30-59 days', '3.00%')}</tr>"
            f"<tr>{_cells('60-89 days', '1.50%')}</tr>"
            "</table>"
        )
        out = tp.parse_trust_metrics(html)
        assert out["delinq_30plus_rate"] == 5.0
        assert out["delinq_60plus_rate"] == 1.5


# ─── discovery / fetch flow ──────────────────────────────────────────────────
def _efts_hit(adsh, doc, name, cik, period="2026-04-30", filed="2026-05-15"):
    return {
        "_id": f"{adsh}:{doc}",
        "_source": {
            "adsh": adsh,
            "display_names": [f"{name}  (CIK {cik:010d})"],
            "period_ending": period,
            "file_date": filed,
        },
    }


REPORT_HTML = (
    "<p>60+-Day Delinquency Rate</p><p>0.96%</p>"
    "<p>Net Charge-Off Rate</p><p>2.39%</p>"
)


class TestFetchFlow:
    @pytest.fixture(autouse=True)
    def no_sleep(self, monkeypatch):
        monkeypatch.setattr(tp.time, "sleep", lambda s: None)

    def _mock_filing(self, mocked_responses, cik, acc, docs):
        acc_nodash = acc.replace("-", "")
        base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}"
        mocked_responses.get(
            f"{base}/index.json",
            json={"directory": {"item": [{"name": d} for d in docs]}},
        )
        for d in docs:
            if d.endswith(".htm"):
                mocked_responses.get(f"{base}/{d}", body=REPORT_HTML)

    def test_fetch_stores_rows(self, fresh_db, mocked_responses):
        acc = "0001140361-26-021526"
        mocked_responses.get(
            tp.BASE_EFTS,
            json={"hits": {"hits": [
                _efts_hit(acc, "ex99-1.htm", "BA Credit Card Trust", 1128250),
            ]}},
        )
        self._mock_filing(
            mocked_responses, 1128250, acc, ["form10-d.htm", "ex99-1.htm"]
        )

        n = tp.fetch_trust_performance(days_back=35)
        assert n == 2  # two metrics in REPORT_HTML

        rows = tp.get_trust_performance()
        assert {r["metric"] for r in rows} == {"delinq_60plus_rate", "net_charge_off_rate"}
        assert rows[0]["trust_name"] == "BA Credit Card Trust"
        assert rows[0]["period_end"] == "2026-04-30"
        assert rows[0]["segment"] == "credit_card"

    def test_already_parsed_accession_skipped(self, fresh_db, mocked_responses):
        acc = "0001140361-26-021526"
        db.upsert_trust_performance(
            {
                "accession_no": acc, "cik": 1128250, "trust_name": "BA",
                "segment": "credit_card", "period_end": "2026-04-30",
                "filed_at": "2026-05-15", "metric": "net_charge_off_rate",
                "value": 2.39, "url": "u", "fetched_at": "t",
            }
        )
        mocked_responses.get(
            tp.BASE_EFTS,
            json={"hits": {"hits": [
                _efts_hit(acc, "ex99-1.htm", "BA Credit Card Trust", 1128250),
            ]}},
        )
        assert tp.fetch_trust_performance(days_back=35) == 0
        # only the EFTS search call — no index.json / document fetches
        assert all("index.json" not in c.request.url for c in mocked_responses.calls)

    def test_latest_pivots_metrics_per_trust(self, fresh_db):
        for period, acc, value in [
            ("2026-03-31", "acc-1", 2.50),
            ("2026-04-30", "acc-2", 2.39),
        ]:
            db.upsert_trust_performance(
                {
                    "accession_no": acc, "cik": 1, "trust_name": "BA Trust",
                    "segment": "credit_card", "period_end": period,
                    "filed_at": period, "metric": "net_charge_off_rate",
                    "value": value, "url": "u", "fetched_at": "t",
                }
            )
        latest = tp.get_trust_performance_latest()
        assert len(latest) == 1
        assert latest[0]["period_end"] == "2026-04-30"
        assert latest[0]["metrics"] == {"net_charge_off_rate": 2.39}


# ─── helper coverage ─────────────────────────────────────────────────────────
class TestHelpers:
    def test_trust_name_prefers_trust_entity(self):
        names = [
            "Citibank, N.A., as depositor  (CIK 0001522616)",
            "CITIBANK CREDIT CARD ISSUANCE TRUST  (CIK 0001108348)",
        ]
        assert tp._trust_name(names) == "CITIBANK CREDIT CARD ISSUANCE TRUST"
        assert tp._trust_cik(names) == 1108348

    def test_list_filing_docs_exhibits_first(self, mocked_responses):
        mocked_responses.get(
            "https://www.sec.gov/Archives/edgar/data/1/000000000012345678/index.json",
            json={"directory": {"item": [
                {"name": "0000000000-12-345678-index.html"},
                {"name": "form10-d.htm"},
                {"name": "ex99-1.htm"},
                {"name": "data.xml"},
            ]}},
        )
        docs = tp._list_filing_docs(1, "0000000000-12-345678")
        assert docs == ["ex99-1.htm", "form10-d.htm"]
