"""
backend/data/trust_performance.py — monthly master-trust performance from 10-Ds.

Credit-card master trusts (Chase Issuance Trust, BA Credit Card Trust, Citibank
Credit Card Issuance Trust, Discover Card Execution Note Trust, WFN/Comenity,
American Express…) file a 10-D every month whose EX-99 distribution report
carries portfolio-level delinquency, charge-off, payment-rate and yield
figures. That is consumer-credit performance ~2 quarters ahead of the
quarterly FRED bank-call-report series (DRCCLACBS / CORCCACBS).

Token-free pipeline, same shape as abs_pricing.py:
  1. EDGAR full-text search forms=10-D for the discovery queries,
  2. fetch each hit document and regex-extract labeled percentage metrics,
  3. store one row per (filing, metric) in `trust_performance`.

Labels vary by trust, so each metric has a small family of label regexes.
Values are required to be percentages (a '%' adjacent to the number) — every
target metric is a rate, which keeps dollar amounts from leaking in.
"""

import html as html_mod
import logging
import re
import time
from datetime import datetime, timezone

import requests

from config import load_data_sources, settings
from cache.db import get_conn, upsert_trust_performance
from data.dates import utc_days_ago_str, utc_today_str

logger = logging.getLogger(__name__)

BASE_EFTS = "https://efts.sec.gov/LATEST/search-index"
HEADERS = {"User-Agent": settings.EDGAR_USER_AGENT, "Accept": "application/json"}

_DEFAULT_QUERIES = ["credit card"]

# metric key -> label regexes, tried in order. All values are percentages.
_METRIC_PATTERNS: dict[str, list[re.Pattern]] = {
    "delinq_30plus_rate": [re.compile(r"30\+\s*-?\s*day delinquency rate", re.I)],
    "delinq_60plus_rate": [re.compile(r"60\+\s*-?\s*day delinquency rate", re.I)],
    "delinq_90plus_rate": [re.compile(r"90\+\s*-?\s*day delinquency rate", re.I)],
    "gross_charge_off_rate": [
        re.compile(r"total charge[-‐‑–— ]?offs? as a percentage of average principal receivables", re.I),
        re.compile(r"gross losses as a percentage of average pool balance", re.I),
        re.compile(r"gross charge[-‐‑–— ]?off rate", re.I),
    ],
    "net_charge_off_rate": [
        re.compile(r"net charge[-‐‑–— ]?offs? as a percentage of average principal receivables", re.I),
        re.compile(r"net losses as a percentage of average pool balance", re.I),
        re.compile(r"net credit losses", re.I),
        re.compile(r"net charge[-‐‑–— ]?off rate", re.I),
        re.compile(r"net loss rate", re.I),
        # Citi reports losses only inside the yield decomposition: Portfolio
        # Yield = Yield Component − Credit Loss Component (annualized net).
        re.compile(r"credit loss component", re.I),
    ],
    "payment_rate": [
        re.compile(r"(?:monthly\s+)?principal payment rate", re.I),
        re.compile(r"\bpayment rate\b", re.I),
        re.compile(r"collections as a percentage of prior month principal", re.I),
    ],
    "portfolio_yield": [re.compile(r"portfolio yield", re.I)],
    "excess_spread_rate": [re.compile(r"excess spread (?:percentage|rate)", re.I)],
    "base_rate": [re.compile(r"\bbase rate\b", re.I)],
}

# A label occurrence preceded by this within 40 chars is a trailing average,
# not the spot monthly value; used only as a fallback when no spot value parses.
_AVG_PREFIX = re.compile(r"(?:three|3)[\s-]*month average\s*$", re.I)

# Occurrences preceded by these are a different metric entirely — skip them
# (e.g. WFN's "Gross Portfolio Yield" vs the net "Portfolio Yield").
_SKIP_PREFIX = {"portfolio_yield": re.compile(r"gross\s*$", re.I)}

# The value must be a percent: a number with only whitespace before a '%'.
_PCT_NUM = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")

_WINDOW = 220  # chars after the label end to look for the value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _linearize(raw_html: str) -> str:
    """Strip tags to newline-separated text suitable for label/value scanning."""
    text = html_mod.unescape(raw_html)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*", "\n", text)


# Delinquency bucket rows, for trusts that report a bucket table instead of a
# headline "30+-day delinquency rate": Chase reports each bucket as a % of pool
# balance ("30-59 days … 0.24%"); Citi reports dollar amounts ("31-60 days
# delinquent $76,622,784") alongside a "Current" balance.
_BUCKET_LABEL = re.compile(
    r"\b(\d{1,3})\s*(?:(?:[-–—]|to)\s*(\d{1,3})|\s*\+|\s+or\s+more)\s*\n?\s*days(?:\s+delinquent|\s+or\s+more)?\b",
    re.I,
)
_COMMA_NUM = re.compile(r"\$?\s*([\d,]{5,})(?:\.\d+)?")
_CURRENT_AMT = re.compile(r"\bCurrent\b\s*\n?\$?\s*\n?([\d,]{5,})")
# Some trusts print two percentages per bucket row — % of accounts, then % of
# receivables after the dollar amount (e.g. WF: "4,537 0.14% $37,024,531 0.40%").
# The receivables-based one is the standard delinquency rate, so prefer the %
# that directly follows a $ amount.
_PCT_AFTER_DOLLAR = re.compile(r"\$\s*[\d,]+(?:\.\d+)?\s*(\d{1,3}(?:\.\d+)?)\s*%")


def _parse_delinq_buckets(text: str) -> dict[str, float]:
    """Compute 30+/60+/90+ delinquency rates from a bucket table."""
    buckets: dict[int, dict] = {}  # low day bound -> {pct, amt, plus}
    for m in _BUCKET_LABEL.finditer(text):
        low = int(m.group(1))
        if low in buckets or low > 360:
            continue
        window = text[m.end() : m.end() + 120].replace("\n", " ")
        pct = _PCT_AFTER_DOLLAR.search(window) or _PCT_NUM.search(window)
        amts = [float(a.group(1).replace(",", "")) for a in _COMMA_NUM.finditer(window)]
        buckets[low] = {
            "pct": float(pct.group(1)) if pct else None,
            "amt": max(amts) if amts else None,
            "plus": m.group(2) is None,
        }
    # An open-ended bucket with granular buckets above it is a summary row
    # (e.g. WFN's "60+ days delinquent" next to 61-90/91-120/…): drop it or
    # the totals double-count.
    for low in [l for l, b in buckets.items() if b["plus"] and any(o > l for o in buckets)]:
        del buckets[low]
    if not buckets:
        return {}

    out: dict[str, float] = {}
    if all(b["pct"] is not None for b in buckets.values()):
        for thresh, key in ((30, "delinq_30plus_rate"), (60, "delinq_60plus_rate"), (90, "delinq_90plus_rate")):
            vals = [b["pct"] for low, b in buckets.items() if low >= thresh]
            if vals:
                out[key] = round(sum(vals), 2)
    elif all(b["amt"] is not None for b in buckets.values()):
        cur = _CURRENT_AMT.search(text)
        if cur:
            total = float(cur.group(1).replace(",", "")) + sum(b["amt"] for b in buckets.values())
            if total > 0:
                for thresh, key in ((30, "delinq_30plus_rate"), (60, "delinq_60plus_rate"), (90, "delinq_90plus_rate")):
                    amt = sum(b["amt"] for low, b in buckets.items() if low >= thresh)
                    if amt or any(low >= thresh for low in buckets):
                        out[key] = round(100.0 * amt / total, 2)
    return out


def parse_trust_metrics(raw_html: str) -> dict[str, float]:
    """Extract labeled percentage metrics from a 10-D distribution report."""
    text = _linearize(raw_html)
    out: dict[str, float] = {}

    for metric, patterns in _METRIC_PATTERNS.items():
        spot: float | None = None
        avg: float | None = None
        skip = _SKIP_PREFIX.get(metric)
        for pat in patterns:
            for m in pat.finditer(text):
                if skip and skip.search(text[max(0, m.start() - 12) : m.start()]):
                    continue
                window = text[m.end() : m.end() + _WINDOW]
                v = _PCT_NUM.search(window.replace("\n", " "))
                if not v:
                    continue
                val = float(v.group(1))
                if not 0 <= val <= 100:
                    continue
                if _AVG_PREFIX.search(text[max(0, m.start() - 40) : m.start()]):
                    avg = avg if avg is not None else val
                else:
                    spot = val
                    break
            if spot is not None:
                break
        value = spot if spot is not None else avg
        if value is not None:
            out[metric] = value

    # Bucket-table fallback: fill any delinquency threshold the headline
    # labels didn't provide (direct labels win on conflict).
    derived = [k for k in _parse_delinq_buckets(text).items() if k[0] not in out]
    out.update(derived)
    # Sanity: rates must be monotonic (30+ ≥ 60+ ≥ 90+). A violation means the
    # bucket table wasn't a clean point-in-time table (e.g. BA's multi-year
    # "Delinquency Experience" history) — discard the derived values.
    trio = [out.get(f"delinq_{t}plus_rate") for t in (30, 60, 90)]
    present = [v for v in trio if v is not None]
    if present != sorted(present, reverse=True):
        for k, _ in derived:
            del out[k]
    return out


def _search_10d(query: str, days_back: int) -> list[dict]:
    params = {
        "q": f'"{query}"',
        "forms": "10-D",
        "startdt": utc_days_ago_str(days_back),
        "enddt": utc_today_str(),
    }
    for attempt in range(3):
        try:
            resp = requests.get(BASE_EFTS, params=params, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            return resp.json().get("hits", {}).get("hits", [])
        except Exception as e:
            if attempt == 2:
                logger.error("trust_performance search error (q=%s): %s", query, e)
            else:
                time.sleep(0.5 * (attempt + 1))
    return []


def _trust_name(display_names: list[str]) -> str:
    """Prefer the display name that is the trust itself; strip the CIK suffix."""
    names = [n.split("(CIK")[0].strip().rstrip(",") for n in display_names]
    for n in names:
        if "trust" in n.lower():
            return n
    return names[0] if names else ""


def _trust_cik(display_names: list[str]) -> int | None:
    for n in display_names:
        if "trust" in n.lower():
            m = re.search(r"CIK\s+(\d+)", n)
            if m:
                return int(m.group(1))
    m = re.search(r"CIK\s+(\d+)", " ".join(display_names))
    return int(m.group(1)) if m else None


def _doc_url(cik: int, accession: str, doc: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{doc}"


def _list_filing_docs(cik: int, accession: str) -> list[str]:
    """All .htm documents in a filing, exhibits first.

    The FTS hit list only surfaces documents matching the query text; the
    distribution report is often a sibling exhibit that doesn't (e.g. Discover
    names its trust, not 'credit card'). The filing index has everything.
    """
    url = _doc_url(cik, accession, "index.json")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        items = resp.json().get("directory", {}).get("item", [])
    except Exception as e:
        logger.debug("trust_performance index skip %s: %s", url, e)
        return []
    docs = [
        it["name"]
        for it in items
        if it.get("name", "").endswith(".htm") and "index" not in it["name"]
    ]
    return sorted(docs, key=lambda d: ("ex" not in d.lower(), d))


def _already_parsed(accession: str) -> bool:
    with get_conn() as conn:
        return (
            conn.execute(
                "SELECT 1 FROM trust_performance WHERE accession_no = ? LIMIT 1",
                (accession,),
            ).fetchone()
            is not None
        )


def fetch_trust_performance(days_back: int = 35) -> int:
    """Discover recent 10-Ds, parse distribution-report metrics, store rows."""
    cfg = load_data_sources().get("trust_performance", {})
    queries = cfg.get("discovery_queries", _DEFAULT_QUERIES)

    # Group hits by accession: one filing yields several hits (form + exhibits).
    filings: dict[str, dict] = {}
    for q in queries:
        for h in _search_10d(q, days_back):
            src = h.get("_source", {})
            acc = src.get("adsh", "")
            doc = (h.get("_id", "").split(":") + [""])[1]
            if not acc or not doc:
                continue
            f = filings.setdefault(
                acc,
                {
                    "cik": _trust_cik(src.get("display_names", [])),
                    "trust_name": _trust_name(src.get("display_names", [])),
                    "period_end": src.get("period_ending", ""),
                    "filed_at": src.get("file_date", ""),
                    "docs": [],
                },
            )
            if doc not in f["docs"]:
                f["docs"].append(doc)
        time.sleep(0.15)

    now = _now()
    count = 0
    for acc, f in filings.items():
        if f["cik"] is None or _already_parsed(acc):
            continue
        # The metrics often span several EX-99 documents (e.g. Chase splits the
        # delinquency table and the yield/loss table), so parse every document
        # in the filing and merge, first document wins per metric.
        docs = _list_filing_docs(f["cik"], acc) or sorted(
            f["docs"], key=lambda d: ("ex" not in d.lower(), d)
        )
        merged: dict[str, float] = {}
        first_url = ""
        for doc in docs:
            url = _doc_url(f["cik"], acc, doc)
            try:
                resp = requests.get(
                    url, headers={"User-Agent": settings.EDGAR_USER_AGENT}, timeout=20
                )
                resp.raise_for_status()
            except Exception as e:
                logger.debug("trust_performance doc fetch skip %s: %s", url, e)
                time.sleep(0.15)
                continue

            metrics = parse_trust_metrics(resp.text)
            time.sleep(0.15)
            if metrics and not first_url:
                first_url = url
            for k, v in metrics.items():
                merged.setdefault(k, v)

        if not merged:
            continue
        for metric, value in merged.items():
            upsert_trust_performance(
                {
                    "accession_no": acc,
                    "cik": f["cik"],
                    "trust_name": f["trust_name"],
                    "segment": "credit_card",
                    "period_end": f["period_end"],
                    "filed_at": f["filed_at"],
                    "metric": metric,
                    "value": value,
                    "url": first_url,
                    "fetched_at": now,
                }
            )
            count += 1
        logger.info(
            "trust_performance: %s (%s) — %d metrics",
            f["trust_name"], f["period_end"], len(merged),
        )

    logger.info("trust_performance fetch complete — %d rows stored", count)
    return count


def get_trust_performance(
    metric: str | None = None,
    trust: str | None = None,
    limit: int = 500,
) -> list[dict]:
    """Time series rows, oldest first, optionally filtered by metric/trust."""
    sql = (
        "SELECT cik, trust_name, segment, period_end, filed_at, metric, value, url "
        "FROM trust_performance WHERE period_end != ''"
    )
    params: list = []
    if metric:
        sql += " AND metric = ?"
        params.append(metric)
    if trust:
        sql += " AND trust_name LIKE ?"
        params.append(f"%{trust}%")
    sql += " ORDER BY period_end, trust_name LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_trust_performance_latest() -> list[dict]:
    """Latest period per trust with its metrics pivoted into one row."""
    rows = get_trust_performance(limit=5000)
    by_trust: dict[str, dict] = {}
    for r in rows:  # oldest first, so later periods overwrite earlier ones
        t = by_trust.setdefault(r["trust_name"], {"trust_name": r["trust_name"]})
        if r["period_end"] >= t.get("period_end", ""):
            if r["period_end"] != t.get("period_end"):
                t["metrics"] = {}
            t.update(
                {"cik": r["cik"], "period_end": r["period_end"], "url": r["url"]}
            )
            t.setdefault("metrics", {})[r["metric"]] = r["value"]
    return sorted(by_trust.values(), key=lambda t: t["trust_name"])
