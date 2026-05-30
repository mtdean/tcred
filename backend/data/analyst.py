"""
backend/data/analyst.py — macro/credit/structured-finance analyst.

Two surfaces:
  * `generate_briefing()` — one-shot synthesis of a structured snapshot
    (recent digests, current+trending indicators, ABS/BDC/regulatory state,
    market moves) into a markdown narrative + JSON watch list. Persisted to
    `briefings` so the dashboard can list past briefings.
  * `chat_with_briefing()` — multi-turn tool-use chat against a saved briefing.
    The model can pull deeper data (FRED history, ABS spread series, EDGAR
    filings, articles, market history, BDC roll-ups) via read-only tools.
    Chat history is ephemeral — the caller (frontend) holds it in-session.

Model: claude-opus-4-7 with adaptive thinking. Briefing uses `effort=high`;
chat uses `effort=xhigh` per the API skill's agentic-coding/agentic-task
recommendation. System + tools + briefing context are cached via
`cache_control` so each subsequent chat turn only pays for the new message
and tool results.
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import anthropic

from cache import db
from config import settings
from data.analyst_tools import TOOL_DISPATCH, TOOL_SCHEMAS
from data.percentiles import compute_percentile

logger = logging.getLogger(__name__)


MODEL = "claude-opus-4-7"

# Briefing system prompt — frozen content for prompt caching. Tone follows the
# project's terminal-style aesthetic: terse, quantitative, no hedging filler.
_BRIEFING_SYSTEM = """You are a macro, credit, and structured-finance analyst writing a monthly intelligence briefing for an experienced reader who already follows the data.

Your job is SYNTHESIS — connecting moves across the news, indicators, spreads, and filings into a coherent narrative about what regime we're in, what's getting better or worse, and what's worth watching. Avoid restating the numbers; explain what they mean together.

OUTPUT FORMAT (strict):

1. A 250-450 word markdown briefing. Lead with a one-sentence regime read. Then 3-5 short thematic paragraphs (e.g. recession risk, credit conditions, consumer credit, structured finance / ABS, regulatory). Use specific numbers where they advance the argument. No headers, no bullet lists, no preamble like "Here is".

2. After the prose, output a fenced JSON block (```json … ```) containing a `watch_items` array — concrete things to track next month. Each item: `{"title": "...", "severity": "info"|"watch"|"warn", "why": "one sentence"}`. 3-7 items. No other JSON.

Rules:
- Be specific and quantitative. No hedging ("could potentially", "may possibly").
- Do not invent facts not in the snapshot.
- Whenever the snapshot provides a `percentile_5y` for an indicator, frame the level in those terms (e.g. "EBP at 0.45 — 78th percentile of the last 5y") rather than the bare number. That regime context is the point.
- If a signal is mixed or unclear, say so directly and call it out as a watch item.
- Reader knows the jargon. Don't define EBP, OAS, nonaccrual, etc."""


# Chat system prompt — same persona; instructs the model to use tools when the
# user wants depth the briefing doesn't carry.
_CHAT_SYSTEM_BASE = """You are the same macro/credit/structured-finance analyst that wrote the briefing above. You're now in a conversation with the user about the briefing and the underlying data.

You have read-only tools to pull deeper history when the briefing's summary isn't enough — historical indicator series, ABS spread series by asset class and rating, BDC roll-ups, EDGAR filings, scored articles, and market history. Use them when the user asks for specifics, comparisons, or extension of a trend; otherwise answer directly from the briefing and your own reasoning.

Keep responses tight and quantitative. No preamble, no hedging. If a tool returns nothing useful, say so and move on rather than calling another."""


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _briefing_id() -> str:
    return "brf_" + secrets.token_hex(8)


# ── Snapshot assembly ───────────────────────────────────────────────────────

# Indicators we want a current value + 3-month change for, in the snapshot.
# Each entry: (series_id, friendly_label, category).
_SNAPSHOT_INDICATORS: list[tuple[str, str, str]] = [
    ("RECESSION_RISK_ENSEMBLE",   "Recession risk (12mo, ensemble %)",      "recession_risk"),
    ("NYFED_RECESSION_PROB",      "NY Fed yield-curve probit (%)",          "recession_risk"),
    ("EBP_REC_PROB",              "EBP probit (%)",                          "recession_risk"),
    ("NTFS_REC_PROB",             "NTFS logit (%)",                          "recession_risk"),
    ("EBP",                       "Excess Bond Premium (pp)",                "credit"),
    ("GZ_SPREAD",                 "GZ credit spread (pp)",                   "credit"),
    ("NEAR_TERM_FWD_SPREAD",      "Near-term forward spread (pp)",           "rates"),
    ("CFSI",                      "Consumer Financial Stress Index (sd)",    "recession_risk"),
    ("OFR_FSI",                   "OFR Financial Stress Index",              "financial_conditions"),
    ("BIS_CREDIT_GAP_US",         "BIS credit-to-GDP gap (pp)",              "financial_conditions"),
    ("CREDIT_IMPULSE",            "Credit impulse (% of GDP)",               "credit"),
    ("T10Y3M",                    "10y-3m Treasury spread (pp)",             "rates"),
    ("T10Y2Y",                    "10y-2y Treasury spread (pp)",             "rates"),
    ("FEDFUNDS",                  "Fed funds rate (%)",                      "rates"),
    ("DGS10",                     "10y Treasury yield (%)",                  "rates"),
    ("DGS2",                      "2y Treasury yield (%)",                   "rates"),
    ("DRCCLACBS",                 "Credit-card delinquency 90+ (%)",         "consumer_credit"),
    ("DRSFRMACBS",                "Single-family mortgage delinquency (%)",  "consumer_credit"),
    ("DRCLACBS",                  "Consumer-loan delinquency (%)",           "consumer_credit"),
    ("CORCCACBS",                 "Credit-card net charge-off (%)",          "consumer_credit"),
    ("CORALACBS",                 "All-loans net charge-off (%)",            "consumer_credit"),
]

# Tickers we want 1m/3m/12m % moves for.
_SNAPSHOT_TICKERS = ["SPY", "QQQ", "IWM", "VIX", "HYG", "LQD", "IEF", "TLT", "JNK"]


def _latest_and_delta(series_id: str, days_lookback: int = 95) -> Optional[dict]:
    """Latest value + change vs ~3 months ago + 5y percentile + 5y range.

    Percentile lets the briefing say "EBP at the 78th percentile of the last 5y"
    instead of just citing the level — that's the regime context the analyst
    is supposed to bring.
    """
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT date, value FROM metrics WHERE series_id = ? AND value IS NOT NULL "
            "ORDER BY date DESC LIMIT 400",
            (series_id,),
        ).fetchall()
    if not rows:
        return None
    latest_date, latest_val = rows[0]["date"], float(rows[0]["value"])
    cutoff = (datetime.fromisoformat(latest_date) - timedelta(days=days_lookback)).date().isoformat()
    prior = next((r for r in rows if r["date"] <= cutoff), None)
    delta = round(latest_val - float(prior["value"]), 4) if prior else None

    pct = compute_percentile(series_id, window_days=1825)
    if pct is not None and pct.get("n_obs", 0) >= 8:
        regime = {
            "percentile_5y": pct["percentile"],
            "min_5y": round(pct["min"], 4),
            "max_5y": round(pct["max"], 4),
            "median_5y": round(pct["median"], 4),
            "n_obs_5y": pct["n_obs"],
        }
    else:
        regime = {}

    return {
        "latest": round(latest_val, 4),
        "as_of": latest_date,
        "delta_3mo": delta,
        **regime,
    }


def _market_moves(ticker: str) -> Optional[dict]:
    """Latest price + 1m/3m/12m % moves, from the metrics table (mkt_<ticker>)."""
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT date, value FROM metrics WHERE series_id = ? AND value IS NOT NULL "
            "ORDER BY date DESC LIMIT 400",
            (f"mkt_{ticker}",),
        ).fetchall()
    if not rows:
        return None
    out = {"latest": round(float(rows[0]["value"]), 2), "as_of": rows[0]["date"]}
    for label, days in (("pct_1m", 30), ("pct_3m", 90), ("pct_12m", 365)):
        cutoff = (datetime.fromisoformat(rows[0]["date"]) - timedelta(days=days)).date().isoformat()
        prior = next((r for r in rows if r["date"] <= cutoff), None)
        if prior and prior["value"]:
            out[label] = round((float(rows[0]["value"]) / float(prior["value"]) - 1.0) * 100.0, 2)
        else:
            out[label] = None
    return out


def _abs_spread_changes(window_days: int = 90) -> list[dict]:
    """Per (segment, seniority) most recent deal spread + change vs trailing average."""
    from data.abs_pricing import get_abs_spread_momentum_deltas

    try:
        rows = get_abs_spread_momentum_deltas()
    except Exception as e:
        logger.warning("ABS spread momentum unavailable: %s", e)
        return []

    cutoff = (datetime.now() - timedelta(days=window_days)).date().isoformat()
    recent = [r for r in rows if r.get("pricing_date") and r["pricing_date"] >= cutoff]
    # Last observation per (segment, seniority).
    by_key: dict[tuple[str, str], dict] = {}
    for r in recent:
        key = (r["segment"], r["seniority"])
        cur = by_key.get(key)
        if cur is None or r["pricing_date"] > cur["pricing_date"]:
            by_key[key] = r
    out = []
    for (segment, seniority), r in by_key.items():
        out.append({
            "segment": segment,
            "seniority": seniority,
            "latest_spread_bps": r.get("spread_bps"),
            "delta_vs_prior_deal_bps": r.get("delta_bps"),
            "zscore_of_delta": r.get("zscore"),
            "pricing_date": r.get("pricing_date"),
        })
    out.sort(key=lambda x: (x["segment"], x["seniority"]))
    return out


def _bdc_state() -> Optional[dict]:
    """Aggregate BDC stress: latest-period roll-up + QoQ change."""
    try:
        from data.bdc import get_bdc_aggregate_trend
        trend = get_bdc_aggregate_trend()
    except Exception as e:
        logger.warning("BDC trend unavailable: %s", e)
        return None
    if not trend:
        return None
    by_period = sorted(trend, key=lambda r: r.get("period") or "")
    latest = by_period[-1]
    prior = by_period[-2] if len(by_period) >= 2 else None

    def _delta(field: str) -> Optional[float]:
        if not prior or latest.get(field) is None or prior.get(field) is None:
            return None
        return round(float(latest[field]) - float(prior[field]), 4)

    return {
        "latest_period": latest.get("period"),
        "n_bdcs": latest.get("n_bdcs"),
        "nonaccrual_rate_fv_pct": latest.get("avg_nonaccrual_rate_fv"),
        "nonaccrual_rate_qoq_delta": _delta("avg_nonaccrual_rate_fv"),
        "wa_interest_rate_pct": latest.get("avg_wa_interest_rate"),
        "pct_first_lien": latest.get("avg_pct_first_lien"),
    }


def _edgar_recent(days: int = 30) -> dict:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    with db.get_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM edgar_filings WHERE filed_at >= ?", (cutoff,)
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT asset_class, COUNT(*) AS n FROM edgar_filings "
            "WHERE filed_at >= ? AND asset_class IS NOT NULL "
            "GROUP BY asset_class ORDER BY n DESC LIMIT 10",
            (cutoff,),
        ).fetchall()
    return {
        "window_days": days,
        "total_filings": int(total),
        "by_asset_class": [{"asset_class": r["asset_class"], "n": int(r["n"])} for r in rows],
    }


def _regulatory_recent(days: int = 30, min_score: int = 3) -> dict:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT agency, action_type, title, publication_date, relevance_score "
            "FROM regulatory_actions WHERE publication_date >= ? "
            "AND relevance_score IS NOT NULL AND relevance_score >= ? "
            "ORDER BY relevance_score DESC, publication_date DESC LIMIT 12",
            (cutoff, min_score),
        ).fetchall()
    return {
        "window_days": days,
        "min_score": min_score,
        "items": [
            {
                "agency": r["agency"],
                "action_type": r["action_type"],
                "title": r["title"],
                "date": r["publication_date"],
                "score": r["relevance_score"],
            }
            for r in rows
        ],
    }


def _recent_digests(days: int = 14) -> list[dict]:
    """Recent AM/PM news digests — the news synthesis layer the analyst wraps."""
    try:
        digests = db.get_digests(limit=days * 2)
    except Exception as e:
        logger.warning("digests unavailable: %s", e)
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    out = []
    for d in digests:
        if (d.get("date") or "") < cutoff:
            continue
        out.append({
            "date": d.get("date"),
            "session": d.get("session"),
            "article_count": d.get("article_count"),
            "summary": d.get("summary"),
        })
    return out


def build_snapshot(period_label: Optional[str] = None) -> dict:
    """Assemble the structured input the analyst will reason over."""
    now = datetime.now(timezone.utc)
    if period_label is None:
        period_label = now.strftime("%Y-%m")

    indicators: dict[str, dict] = {}
    for series_id, label, category in _SNAPSHOT_INDICATORS:
        v = _latest_and_delta(series_id)
        if v is not None:
            indicators[series_id] = {"label": label, "category": category, **v}

    market: dict[str, dict] = {}
    for ticker in _SNAPSHOT_TICKERS:
        v = _market_moves(ticker)
        if v is not None:
            market[ticker] = v

    return {
        "period_label": period_label,
        "as_of": now.isoformat(),
        "indicators": indicators,
        "market_moves": market,
        "abs_spread_changes_recent": _abs_spread_changes(),
        "bdc_state": _bdc_state(),
        "edgar_recent": _edgar_recent(),
        "regulatory_recent": _regulatory_recent(),
        "news_digests_recent": _recent_digests(),
    }


# ── Briefing generation ─────────────────────────────────────────────────────

def _render_snapshot_for_prompt(snapshot: dict) -> str:
    """Turn the snapshot into a clearly-labeled text block. JSON is fine for
    Claude but a labeled rendering is easier to scan and cheaper on tokens."""
    return (
        f"# Snapshot — {snapshot['period_label']} (as of {snapshot['as_of']})\n\n"
        + "Below is the full structured snapshot as JSON. Reason over it directly.\n\n"
        + "```json\n"
        + json.dumps(snapshot, indent=2, default=str)
        + "\n```\n"
    )


def _extract_watch_items(briefing_md: str) -> tuple[str, Optional[list[dict]]]:
    """Split the prose narrative from the trailing ```json watch_items block."""
    if "```json" not in briefing_md:
        return briefing_md.strip(), None
    head, _, tail = briefing_md.rpartition("```json")
    if "```" not in tail:
        return briefing_md.strip(), None
    json_text, _, _ = tail.partition("```")
    try:
        parsed = json.loads(json_text.strip())
    except json.JSONDecodeError:
        return briefing_md.strip(), None
    items = parsed.get("watch_items") if isinstance(parsed, dict) else None
    return head.rstrip(), items if isinstance(items, list) else None


class BriefingError(Exception):
    """Raised when a briefing cannot be produced (missing key, model error)."""


def generate_briefing(period_label: Optional[str] = None) -> dict:
    """Build a snapshot, ask Opus 4.7 to synthesize, persist + return the briefing.

    Returns the saved briefing dict (same shape as `get_briefing`).
    """
    if not settings.ANTHROPIC_API_KEY:
        raise BriefingError("ANTHROPIC_API_KEY not set — cannot generate briefing.")

    snapshot = build_snapshot(period_label=period_label)
    prompt = _render_snapshot_for_prompt(snapshot)

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    try:
        # Stream the response — adaptive thinking + a multi-paragraph briefing can
        # run several thousand tokens; streaming avoids SDK HTTP timeouts.
        with client.messages.stream(
            model=MODEL,
            max_tokens=8000,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            system=_BRIEFING_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            message = stream.get_final_message()
    except anthropic.APIError as e:
        raise BriefingError(f"Claude API error: {e}") from e

    raw = next((b.text for b in message.content if b.type == "text"), "")
    if not raw:
        raise BriefingError("Empty briefing returned by the model.")
    briefing_md, watch_items = _extract_watch_items(raw)

    row = {
        "id": _briefing_id(),
        "period_label": snapshot["period_label"],
        "generated_at": _now_utc_iso(),
        "model": MODEL,
        "briefing_md": briefing_md,
        "watch_items": json.dumps(watch_items) if watch_items else None,
        "snapshot_json": json.dumps(snapshot, default=str),
        "input_tokens": getattr(message.usage, "input_tokens", None),
        "output_tokens": getattr(message.usage, "output_tokens", None),
        "cache_read_tokens": getattr(message.usage, "cache_read_input_tokens", None),
        "cache_write_tokens": getattr(message.usage, "cache_creation_input_tokens", None),
    }
    db.insert_briefing(row)
    logger.info("Briefing generated: %s (%d in / %d out tokens)",
                row["id"], row["input_tokens"] or 0, row["output_tokens"] or 0)
    return db.get_briefing(row["id"])


# ── Chat layer ──────────────────────────────────────────────────────────────

def _chat_system_blocks(briefing: dict) -> list[dict]:
    """System prompt = persona + the briefing's prose + the snapshot it was built
    on. Marked cacheable so subsequent turns in the same chat reuse the prefix."""
    briefing_block = (
        f"# BRIEFING — {briefing['period_label']} "
        f"(generated {briefing['generated_at']})\n\n"
        f"{briefing['briefing_md']}\n\n"
        f"# SNAPSHOT used to write the briefing\n\n"
        f"```json\n{briefing['snapshot_json']}\n```\n"
    )
    return [
        {"type": "text", "text": _CHAT_SYSTEM_BASE},
        {
            "type": "text",
            "text": briefing_block,
            "cache_control": {"type": "ephemeral"},
        },
    ]


def _dispatch_tool(name: str, tool_input: dict) -> str:
    handler = TOOL_DISPATCH.get(name)
    if handler is None:
        return json.dumps({"error": f"unknown tool '{name}'"})
    try:
        result = handler(**(tool_input or {}))
    except TypeError as e:
        return json.dumps({"error": f"bad arguments for {name}: {e}"})
    except Exception as e:  # noqa: BLE001 — return the error to the model
        logger.warning("tool %s failed: %s", name, e)
        return json.dumps({"error": str(e)})
    return json.dumps(result, default=str)


class ChatError(Exception):
    pass


def chat_with_briefing(
    briefing_id: str,
    history: list[dict],
    user_message: str,
    max_tool_iterations: int = 6,
) -> dict:
    """Run one chat turn against a saved briefing.

    `history` is the client-held conversation so far — a list of
    `{"role": "user"|"assistant", "content": str}` dicts. The caller is
    responsible for persistence (we hold no chat state server-side).

    Returns `{"reply": str, "tool_calls": [{name, input}], "usage": {...}}`.
    """
    if not settings.ANTHROPIC_API_KEY:
        raise ChatError("ANTHROPIC_API_KEY not set — cannot chat.")

    briefing = db.get_briefing(briefing_id)
    if briefing is None:
        raise ChatError(f"briefing {briefing_id!r} not found")

    # Build the message list: prior turns (text only, the client doesn't see
    # tool blocks) plus the new user message.
    messages: list[dict[str, Any]] = []
    for h in history:
        role = h.get("role")
        content = h.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    system_blocks = _chat_system_blocks(briefing)

    tool_calls_seen: list[dict] = []
    total_input = total_output = 0
    cache_read = cache_write = 0

    for _ in range(max_tool_iterations):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=8000,
                thinking={"type": "adaptive"},
                output_config={"effort": "xhigh"},
                system=system_blocks,
                tools=TOOL_SCHEMAS,
                messages=messages,
            )
        except anthropic.APIError as e:
            raise ChatError(f"Claude API error: {e}") from e

        total_input += getattr(response.usage, "input_tokens", 0) or 0
        total_output += getattr(response.usage, "output_tokens", 0) or 0
        cache_read += getattr(response.usage, "cache_read_input_tokens", 0) or 0
        cache_write += getattr(response.usage, "cache_creation_input_tokens", 0) or 0

        if response.stop_reason == "end_turn":
            text = next((b.text for b in response.content if b.type == "text"), "")
            return {
                "reply": text,
                "tool_calls": tool_calls_seen,
                "usage": {
                    "input_tokens": total_input,
                    "output_tokens": total_output,
                    "cache_read_input_tokens": cache_read,
                    "cache_creation_input_tokens": cache_write,
                },
            }

        if response.stop_reason != "tool_use":
            text = next((b.text for b in response.content if b.type == "text"), "")
            return {
                "reply": text or f"[stopped: {response.stop_reason}]",
                "tool_calls": tool_calls_seen,
                "usage": {
                    "input_tokens": total_input,
                    "output_tokens": total_output,
                    "cache_read_input_tokens": cache_read,
                    "cache_creation_input_tokens": cache_write,
                },
            }

        # Execute every tool_use block in this response, append both the
        # assistant message (with tool_use blocks) and the user tool_result
        # message to history, then loop.
        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            tool_calls_seen.append({"name": block.name, "input": block.input})
            result_text = _dispatch_tool(block.name, block.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result_text,
            })
        messages.append({"role": "user", "content": tool_results})

    raise ChatError(
        f"tool-use loop exceeded {max_tool_iterations} iterations without an end_turn"
    )
