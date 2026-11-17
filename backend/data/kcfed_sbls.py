"""
backend/data/kcfed_sbls.py — Kansas City Fed Small Business Lending Survey (FR 2028D).

Quarterly survey of ~130-170 mostly community banks on their *small-business
C&I* lending (no CRE). The landing page links a single "Aggregate Data" XLSX
holding the full history (2017:Q4 →) in long format on the "Aggregate Data
Tab" sheet:

    Tab | Sub Item | Period ("2026:Q2") | Attribute | Value | ...

Tab codes follow the FR 2028D form (see the "Aggregate Data Definition
Table" sheet). The file name is re-dated every release, so we scrape the page
for the current link (manheim.py pattern). Labels in the file carry stray
trailing spaces ('A19 ') — everything is stripped before matching.

Stored in `metrics` (category 'kcfed_sbls'); percents as percent (7.52 = 7.52%):
  KCF_SBLS_RATE_FIXED_TERM   new fixed-rate term loans, wtd-avg rate (A16)
  KCF_SBLS_RATE_VAR_TERM     new variable-rate term loans, wtd-avg rate (A19)
  KCF_SBLS_RATE_FIXED_LOC    new fixed-rate loans under commitment (A25)
  KCF_SBLS_RATE_VAR_LOC      new variable-rate loans under commitment (A28)
  KCF_SBLS_NEW_VOL           new-loan $ across A16/A19/A25/A28, $ millions
  KCF_SBLS_NEW_VOL_PER_BANK  same, averaged per responding bank, $ millions
  KCF_SBLS_STANDARDS_NET     net % of banks tightening credit standards (D1)
  KCF_SBLS_DEMAND_NET        net % reporting stronger loan demand (C1)
  KCF_SBLS_LINE_USAGE_NET    net % reporting higher credit-line usage (B1)
  KCF_SBLS_APPLICANT_QUAL_NET net % reporting improved applicant quality (E1)
  KCF_SBLS_APPROVAL_RATE     % of application $ approved (C2)

Caveats: the respondent panel changes quarter to quarter and past quarters
get revised, so the dollar-volume series is sample-sensitive (prefer the
per-bank figure) — and 2020:Q2/2021:Q1 are dominated by PPP loans (fixed
term rate ~1%, volume 10-20x normal). Published ~2 months after quarter end.
"""

from __future__ import annotations

import io
import logging
import re
from urllib.parse import urljoin

import pandas as pd
import requests

from data.sources import Source, store_series

logger = logging.getLogger(__name__)

PAGE_URL = "https://www.kansascityfed.org/surveys/small-business-lending-survey/"
_XLSX_RE = re.compile(r'href="([^"]*Aggregate[-_ ]?Data[^"]*\.xlsx)"', re.I)
# Akamai on kansascityfed.org tarpits most custom User-Agents (requests just
# hang); the library default goes through, so we deliberately don't set one.
_TIMEOUT = 60
CATEGORY = "kcfed_sbls"

# (series_id, label, tab) — weighted-average interest rate on new loans.
_RATES = [
    ("KCF_SBLS_RATE_FIXED_TERM", "KC Fed SBLS: Rate on New Fixed-Rate Term Loans (%)", "A16"),
    ("KCF_SBLS_RATE_VAR_TERM", "KC Fed SBLS: Rate on New Variable-Rate Term Loans (%)", "A19"),
    ("KCF_SBLS_RATE_FIXED_LOC", "KC Fed SBLS: Rate on New Fixed-Rate Loans Under Commitment (%)", "A25"),
    ("KCF_SBLS_RATE_VAR_LOC", "KC Fed SBLS: Rate on New Variable-Rate Loans Under Commitment (%)", "A28"),
]
_NEW_LOAN_TABS = ("A16", "A19", "A25", "A28")

# (series_id, label, tab, positive-answer prefixes, negative-answer prefixes)
# Net % = share(positive) - share(negative), x100.
_DIFFUSION = [
    ("KCF_SBLS_STANDARDS_NET", "KC Fed SBLS: Net % Banks Tightening Standards", "D1",
     ("tightened",), ("eased",)),
    ("KCF_SBLS_DEMAND_NET", "KC Fed SBLS: Net % Reporting Stronger Loan Demand", "C1",
     ("substantially stronger", "moderately stronger"), ("substantially weaker", "moderately weaker")),
    ("KCF_SBLS_LINE_USAGE_NET", "KC Fed SBLS: Net % Reporting Higher Credit-Line Usage", "B1",
     ("increased",), ("decreased",)),
    ("KCF_SBLS_APPLICANT_QUAL_NET", "KC Fed SBLS: Net % Reporting Improved Applicant Quality", "E1",
     ("improved",), ("declined",)),
]


def _quarter_date(period) -> str | None:
    m = re.match(r"\s*(\d{4})\s*:?\s*Q([1-4])\s*$", str(period), re.I)
    if not m:
        return None
    return f"{m.group(1)}-{(int(m.group(2)) - 1) * 3 + 1:02d}-01"


def find_xlsx_url(html: str) -> str | None:
    m = _XLSX_RE.search(html)
    return urljoin(PAGE_URL, m.group(1)) if m else None


def load_long(content: bytes) -> pd.DataFrame:
    """Read the long-format data sheet → columns tab/sub/date/attr/value."""
    xl = pd.ExcelFile(io.BytesIO(content))
    sheet = next((s for s in xl.sheet_names if "data tab" in s.lower()), None)
    if sheet is None:
        raise ValueError(f"KC Fed SBLS: no 'Aggregate Data Tab' sheet in {xl.sheet_names}")
    raw = pd.read_excel(xl, sheet)
    cols = {str(c).strip().lower(): c for c in raw.columns}
    need = {"tab": "tab", "sub item": "sub", "period": "period", "attribute": "attr", "value": "value"}
    missing = [k for k in need if k not in cols]
    if missing:
        raise ValueError(f"KC Fed SBLS: missing columns {missing} in {list(raw.columns)}")
    df = raw[[cols[k] for k in need]].copy()
    df.columns = list(need.values())
    for c in ("tab", "sub", "attr"):
        df[c] = df[c].astype("string").str.strip().str.replace(r"\s+", " ", regex=True)
    df["sub"] = df["sub"].fillna("")
    df["tab"] = df["tab"].str.upper().str.replace(".", "", regex=False)
    df["date"] = df["period"].map(_quarter_date)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df.dropna(subset=["date", "value"])


def _attr(df: pd.DataFrame, tab: str, attr: str, sub: str = "") -> pd.Series:
    """date-indexed values for one (tab, sub item, attribute)."""
    s = df[(df["tab"] == tab) & (df["sub"].str.lower() == sub.lower())
           & (df["attr"].str.lower() == attr.lower())]
    return s.groupby("date")["value"].first()


def compute_series(df: pd.DataFrame) -> dict[str, tuple[str, list[tuple[str, float]]]]:
    """Return {series_id: (label, [(date, value), ...])}."""
    out: dict[str, tuple[str, list[tuple[str, float]]]] = {}

    def put(sid, label, s: pd.Series):
        s = s.dropna().sort_index()
        if len(s):
            out[sid] = (label, [(d, round(float(v), 4)) for d, v in s.items()])

    for sid, label, tab in _RATES:
        put(sid, label, _attr(df, tab, "Weighted average interest rate"))

    # Volume: dollar amounts are in $ thousands.
    vols = [_attr(df, t, "Outstanding dollar amount") for t in _NEW_LOAN_TABS]
    resps = [_attr(df, t, "Number of responses") for t in _NEW_LOAN_TABS]
    total = pd.concat(vols, axis=1).sum(axis=1, min_count=1) / 1000
    per_bank = pd.concat(
        [v / r.where(r > 0) for v, r in zip(vols, resps)], axis=1
    ).sum(axis=1, min_count=1) / 1000
    put("KCF_SBLS_NEW_VOL", "KC Fed SBLS: New Small-Business Loan Volume ($mn, sample total)", total)
    put("KCF_SBLS_NEW_VOL_PER_BANK", "KC Fed SBLS: New Small-Business Loan Volume per Bank ($mn)", per_bank)

    for sid, label, tab, pos, neg in _DIFFUSION:
        s = df[(df["tab"] == tab) & (df["sub"] == "")]
        a = s["attr"].str.lower()
        p = s[a.str.startswith(pos)].groupby("date")["value"].sum()
        n = s[a.str.startswith(neg)].groupby("date")["value"].sum()
        idx = p.index.union(n.index)
        put(sid, label, (p.reindex(idx, fill_value=0) - n.reindex(idx, fill_value=0)) * 100)

    # Shares are stored as fractions (0.6059) → percent.
    put("KCF_SBLS_APPROVAL_RATE", "KC Fed SBLS: Share of Application $ Approved (%)",
        _attr(df, "C2", "Dollar amount", sub="Applications Approved") * 100)
    return out


def fetch_kcfed_sbls() -> int:
    """Scrape the current XLSX link, parse, store. Returns rows written."""
    page = requests.get(PAGE_URL, timeout=_TIMEOUT)
    page.raise_for_status()
    url = find_xlsx_url(page.text)
    if not url:
        raise RuntimeError(f"KC Fed SBLS: no Aggregate Data XLSX link on {PAGE_URL}")

    resp = requests.get(url, timeout=_TIMEOUT)
    resp.raise_for_status()
    series = compute_series(load_long(resp.content))
    if not series:
        raise ValueError(f"KC Fed SBLS: no series parsed from {url}")

    n = sum(store_series(sid, label, pts, category=CATEGORY) for sid, (label, pts) in series.items())
    latest = max(pts[-1][0] for _, pts in series.values())
    logger.info("KC Fed SBLS: %d rows across %d series (latest %s) from %s",
                n, len(series), latest, url)
    return n


# Registered with the scheduler via data/sources.py (BUILTIN_MODULES).
# Quarterly release; a weekly check picks it up within days.
SOURCE = Source(
    id="kcfed_sbls",
    label="KC Fed Small Business Lending Survey",
    fetch=fetch_kcfed_sbls,
    every_hours=24 * 7,
    description="Quarterly KC Fed FR 2028D survey: small-business C&I loan rates, "
                "volumes, standards, demand (aggregate XLSX, 2017:Q4 →).",
    # Charted by hand-built panels in Macro → Business & SMB Credit.
)
