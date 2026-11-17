"""
Cover data/kcfed_sbls.py — KC Fed Small Business Lending Survey ingest.
"""

from __future__ import annotations

import io

import pandas as pd
import pytest

from cache import db
from data import kcfed_sbls as k

XLSX_URL = ("https://www.kansascityfed.org/documents/18908/"
            "Small-Business-Lending-Survey-Aggregate-Data-Kansas-City-Fed-Second-Quarter-2026.xlsx")
PAGE_HTML = (
    '<a href="/documents/11718/Small_Business_Lending_Survey_FR_2028D_-_Form.pdf">form</a>'
    '<a href="/documents/18908/Small-Business-Lending-Survey-Aggregate-Data-'
    'Kansas-City-Fed-Second-Quarter-2026.xlsx">Aggregate data</a>'
)


def _rows():
    """Long-format rows mirroring the real sheet, incl. its stray whitespace."""
    rows = []

    def add(tab, sub, period, attr, value):
        rows.append([tab, sub, period, attr, value, "x", None, None])

    for period, fixed, var, vol in [("2026:Q1", 7.5896, 7.3387, 2000), ("2026:Q2", 7.5175, 7.3965, 3000)]:
        add("A16", None, period, "Number of responses", 100)
        add("A16", None, period, "Outstanding dollar amount", vol)
        add("A16", None, period, "Weighted average interest rate", fixed)
        add("A19 ", None, period, "Weighted average interest rate ", var)
        add("A19 ", None, period, "Number of responses", 50)
        add("A19 ", None, period, "Outstanding dollar amount", 1000)
        add("A20", "Weighted average interest rate", period, "Median", 99.0)  # must be ignored
        add("D1", None, period, "Number of responses", 146)
        add("D1", None, period, "Tightened considerably", 0.0068)
        add("D1", None, period, "Tightened somewhat", 0.0685)
        add("D1", None, period, "Remained basically unchanged", 0.9247)
        add("D1", None, period, "Eased somewhat", 0.02)
        add("D1", None, period, "Eased considerably", 0)
        add("C1", None, period, "Moderately stronger", 0.1438)
        add("C1", None, period, "Remained basically unchanged", 0.7397)
        add("C1", None, period, "Moderately weaker", 0.1164)
        add("C2", "Applications Received", period, "Dollar amount", 16294793)
        add("C2", "Applications Approved", period, "Number", 0.4593)
        add("C2", "Applications Approved", period, "Dollar amount", 0.6059)
    add("A16", None, "2026:Q2", "Maximum maturity", "N/A")  # stray text value
    return rows


def _xlsx(rows=None) -> bytes:
    cols = ["Tab", "Sub Item", "Period", "Attribute", "Value",
            "Tabs Identifier.Aggregate Data Tab", "Secondary Tab", "As Of Date"]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        pd.DataFrame([[None, "Aggregate Data Tab #"]]).to_excel(
            w, sheet_name="Aggregate Data Definition Table", header=False, index=False)
        pd.DataFrame(rows or _rows(), columns=cols).to_excel(
            w, sheet_name="Aggregate Data Tab", index=False)
    return buf.getvalue()


def _series(series_id):
    with db.get_conn() as conn:
        return [
            (r["date"], r["value"]) for r in conn.execute(
                "SELECT date, value FROM metrics WHERE series_id = ? ORDER BY date",
                (series_id,),
            )
        ]


def test_find_xlsx_url_absolutizes_link():
    assert k.find_xlsx_url(PAGE_HTML) == XLSX_URL
    assert k.find_xlsx_url('<a href="/x/form.pdf">pdf</a>') is None


def test_quarter_date():
    assert k._quarter_date("2017:Q4") == "2017-10-01"
    assert k._quarter_date("2026:Q2") == "2026-04-01"
    assert k._quarter_date("Period") is None


def test_fetch_stores_series(fresh_db, mocked_responses):
    mocked_responses.get(k.PAGE_URL, body=PAGE_HTML, status=200)
    mocked_responses.get(XLSX_URL, body=_xlsx(), status=200)

    n = k.SOURCE.fetch()
    assert n > 0

    assert _series("KCF_SBLS_RATE_FIXED_TERM") == [("2026-01-01", 7.5896), ("2026-04-01", 7.5175)]
    # trailing-space tab/attr labels still matched
    assert _series("KCF_SBLS_RATE_VAR_TERM") == [("2026-01-01", 7.3387), ("2026-04-01", 7.3965)]
    # tabs absent from the file produce no series
    assert _series("KCF_SBLS_RATE_VAR_LOC") == []

    # net tightening = (0.0068 + 0.0685 - 0.02) * 100
    assert _series("KCF_SBLS_STANDARDS_NET")[-1][1] == pytest.approx(5.53)
    assert _series("KCF_SBLS_DEMAND_NET")[-1][1] == pytest.approx(2.74)
    assert _series("KCF_SBLS_APPROVAL_RATE")[-1] == ("2026-04-01", pytest.approx(60.59))

    # volume in $mn: (3000 + 1000) / 1000; per bank: 3000/100 + 1000/50 → /1000
    assert _series("KCF_SBLS_NEW_VOL")[-1][1] == pytest.approx(4.0)
    assert _series("KCF_SBLS_NEW_VOL_PER_BANK")[-1][1] == pytest.approx(0.05)

    with db.get_conn() as conn:
        cats = {r[0] for r in conn.execute(
            "SELECT DISTINCT category FROM metrics WHERE series_id LIKE 'KCF_SBLS_%'")}
    assert cats == {"kcfed_sbls"}


def test_fetch_idempotent(fresh_db, mocked_responses):
    for _ in range(2):
        mocked_responses.get(k.PAGE_URL, body=PAGE_HTML, status=200)
        mocked_responses.get(XLSX_URL, body=_xlsx(), status=200)
        k.fetch_kcfed_sbls()
    assert len(_series("KCF_SBLS_RATE_FIXED_TERM")) == 2


def test_missing_link_raises(fresh_db, mocked_responses):
    mocked_responses.get(k.PAGE_URL, body="<html>no data</html>", status=200)
    with pytest.raises(RuntimeError):
        k.fetch_kcfed_sbls()


def test_layout_change_raises(fresh_db, mocked_responses):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        pd.DataFrame({"foo": [1]}).to_excel(w, sheet_name="Aggregate Data Tab", index=False)
    mocked_responses.get(k.PAGE_URL, body=PAGE_HTML, status=200)
    mocked_responses.get(XLSX_URL, body=buf.getvalue(), status=200)
    with pytest.raises(ValueError):
        k.fetch_kcfed_sbls()


def test_source_declaration():
    s = k.SOURCE
    assert s.id == "kcfed_sbls"
    assert s.every_hours == 168
    # Charted by hand-built Macro panels, not the generic Custom Sources one.
    assert s.panel is None
