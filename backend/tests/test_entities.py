"""
Cover data/entities.py — cross-source entity lookup.
"""

from __future__ import annotations

import hashlib

import pytest

from cache import db
from data import entities

NOW = "2026-09-18T12:00:00+00:00"


def _seed_holding(conn, bdc, period, company, cost, fv, nonaccrual=0):
    hid = hashlib.sha256(f"{bdc}|{period}|{company}".encode()).hexdigest()[:16]
    conn.execute(
        "INSERT OR REPLACE INTO bdc_holdings (id, adsh, cik, bdc_name, period, "
        "company_name, investment_type, cost_basis, fair_value, is_nonaccrual, fetched_at) "
        "VALUES (?, 'a', '1', ?, ?, ?, 'First Lien', ?, ?, ?, ?)",
        (hid, bdc, period, company, cost, fv, nonaccrual, NOW),
    )
    conn.commit()


def _seed_filing(conn, accession, company, desc=""):
    conn.execute(
        "INSERT OR REPLACE INTO edgar_filings (accession_no, company_name, form_type, "
        "filed_at, description, url, fetched_at) VALUES (?, ?, '424B5', '2026-09-01', ?, 'http://x', ?)",
        (accession, company, desc, NOW),
    )
    conn.commit()


class TestLookupEntity:
    def test_short_query_rejected(self, fresh_db):
        assert "error" in entities.lookup_entity("ab")

    def test_aggregates_holdings_by_period(self, db_conn):
        blob = "Investments Software Acme Learning, LLC First Lien Term Loan"
        _seed_holding(db_conn, "BDC1", "2026-03-31", blob, 100, 95)
        _seed_holding(db_conn, "BDC2", "2026-03-31", blob, 50, 45)
        _seed_holding(db_conn, "BDC1", "2026-06-30", blob, 100, 80)
        out = entities.lookup_entity("Acme Learning")
        periods = out["holdings_by_period"]
        assert [p["period"] for p in periods] == ["2026-03-31", "2026-06-30"]
        assert periods[0]["n_bdcs"] == 2
        assert periods[0]["mark_to_cost"] == pytest.approx(140 / 150)
        assert out["latest_period"] == "2026-06-30"
        assert len(out["current_holders"]) == 1

    def test_word_boundary_no_substring_leak(self, db_conn):
        # "Ares" must not match "shares" — the watchlist lesson.
        _seed_holding(db_conn, "BDC1", "2026-06-30",
                      "Common shares of Acme Corp", 100, 100)
        _seed_holding(db_conn, "BDC1", "2026-06-30",
                      "Ares Capital JV, LLC First Lien", 100, 100)
        out = entities.lookup_entity("Ares")
        assert len(out["holdings_by_period"]) == 1
        assert out["holdings_by_period"][0]["n_tranches"] == 1

    def test_nonaccrual_flag_propagates(self, db_conn):
        _seed_holding(db_conn, "BDC1", "2026-06-30",
                      "Troubled Co, LLC (non-accrual)", 100, 40, nonaccrual=1)
        out = entities.lookup_entity("Troubled Co")
        assert out["holdings_by_period"][0]["any_nonaccrual"] is True

    def test_filings_matched(self, db_conn):
        _seed_filing(db_conn, "acc-1", "ACME LEARNING TRUST 2026-1")
        _seed_filing(db_conn, "acc-2", "OTHER ISSUER LLC")
        out = entities.lookup_entity("Acme Learning")
        assert len(out["filings"]) == 1
        assert out["filings"][0]["accession_no"] == "acc-1"

    def test_empty_everything(self, fresh_db):
        out = entities.lookup_entity("Nonexistent Co")
        assert out["holdings_by_period"] == []
        assert out["filings"] == []
        assert out["current_holders"] == []
