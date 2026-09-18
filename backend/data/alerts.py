"""
backend/data/alerts.py — threshold alert rules → ntfy push notifications.

Rules live in config/alerts.yaml (re-read every evaluation — edit without a
restart). Pushes go to the ntfy topic in NTFY_TOPIC (.env); without a topic
the whole engine is a silent no-op, same pattern as the Gmail ingest.

Noise controls, in evaluation order:
  1. per-rule `enabled` flag
  2. per-rule cooldown (no repeat within cooldown_hours of the last delivery)
  3. quiet hours (local time window: evaluation happens, delivery doesn't —
     a still-true condition fires on the first run after quiet ends)
  4. global max_per_day cap across all rules (rolling 24h)

Only DELIVERED alerts are recorded in alert_events; cooldowns key off that
table, so a suppressed rule stays armed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import requests
import yaml

from cache.db import get_conn
from config import PROJECT_ROOT, settings

logger = logging.getLogger(__name__)

ALERTS_YAML = PROJECT_ROOT / "config" / "alerts.yaml"

_PRIORITIES = {"min", "low", "default", "high", "urgent"}


def load_alerts_config() -> dict:
    try:
        with open(ALERTS_YAML) as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


# ── Rule evaluators ──────────────────────────────────────────────────────────
# Each returns None (not triggered) or a body string describing what fired.

def _latest_metric(series_id: str) -> Optional[tuple[str, float]]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT date, value FROM metrics WHERE series_id = ? "
            "AND value IS NOT NULL ORDER BY date DESC LIMIT 1",
            (series_id,),
        ).fetchone()
    return (row["date"], float(row["value"])) if row else None


def _eval_metric_threshold(rule: dict) -> Optional[str]:
    latest = _latest_metric(rule["series_id"])
    if latest is None:
        return None
    date, value = latest
    op, threshold = rule.get("op", "gt"), float(rule["value"])
    hit = value > threshold if op == "gt" else value < threshold
    if not hit:
        return None
    sign = ">" if op == "gt" else "<"
    return f"{rule['series_id']} = {value:g} ({sign} {threshold:g}) as of {date}"


def _eval_metric_delta(rule: dict) -> Optional[str]:
    series_id, days, change = rule["series_id"], int(rule["days"]), float(rule["change"])
    latest = _latest_metric(series_id)
    if latest is None:
        return None
    date, value = latest
    cutoff = (
        datetime.fromisoformat(date) - timedelta(days=days)
    ).date().isoformat()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM metrics WHERE series_id = ? AND date <= ? "
            "AND value IS NOT NULL ORDER BY date DESC LIMIT 1",
            (series_id, cutoff),
        ).fetchone()
    if row is None:
        return None
    delta = value - float(row["value"])
    # `change` sign picks the direction: positive watches rises, negative falls.
    hit = delta >= change if change >= 0 else delta <= change
    if not hit:
        return None
    return (
        f"{series_id} moved {delta:+g} over ~{days}d "
        f"(now {value:g}, threshold {change:+g})"
    )


def _eval_job_failures(rule: dict) -> Optional[str]:
    need = int(rule.get("consecutive", 3))
    with get_conn() as conn:
        jobs = [r["job_id"] for r in conn.execute(
            "SELECT DISTINCT job_id FROM job_runs"
        )]
        failing: list[str] = []
        for job_id in jobs:
            rows = conn.execute(
                "SELECT status FROM job_runs WHERE job_id = ? AND status != 'running' "
                "ORDER BY started_at DESC LIMIT ?",
                (job_id, need),
            ).fetchall()
            if len(rows) == need and all(r["status"] == "error" for r in rows):
                failing.append(job_id)
    if not failing:
        return None
    return f"{need}+ consecutive failures: {', '.join(sorted(failing))}"


def _eval_feed_health(rule: dict) -> Optional[str]:
    min_dead = int(rule.get("min_dead", 5))
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT feed_name FROM feed_health WHERE is_live = 0"
        ).fetchall()
    dead = [r["feed_name"] for r in rows]
    if len(dead) < min_dead:
        return None
    listed = ", ".join(sorted(dead)[:6]) + ("…" if len(dead) > 6 else "")
    return f"{len(dead)} feeds down: {listed}"


def _eval_bdc_sector_mark_drop(rule: dict) -> Optional[str]:
    from data.bdc import get_bdc_sector_trend
    drop = float(rule.get("drop_bps", 150)) / 10_000
    rows = get_bdc_sector_trend()
    periods = sorted({r["period"] for r in rows})
    if len(periods) < 2:
        return None
    latest_p, prev_p = periods[-1], periods[-2]
    prev = {r["sector"]: r["mark_to_cost"] for r in rows if r["period"] == prev_p}
    hits = []
    for r in rows:
        if r["period"] != latest_p or r["mark_to_cost"] is None:
            continue
        p = prev.get(r["sector"])
        if p is not None and (p - r["mark_to_cost"]) >= drop:
            hits.append(
                f"{r['sector']} {(p - r['mark_to_cost']) * 10_000:.0f}bps "
                f"(to {r['mark_to_cost']:.3f})"
            )
    if not hits:
        return None
    return f"QoQ mark drops ({prev_p} → {latest_p}): " + "; ".join(hits)


_EVALUATORS: dict[str, Callable[[dict], Optional[str]]] = {
    "metric_threshold": _eval_metric_threshold,
    "metric_delta": _eval_metric_delta,
    "job_failures": _eval_job_failures,
    "feed_health": _eval_feed_health,
    "bdc_sector_mark_drop": _eval_bdc_sector_mark_drop,
}


# ── Delivery gates ───────────────────────────────────────────────────────────

def _in_quiet_hours(cfg: dict, now_local: Optional[datetime] = None) -> bool:
    quiet = (cfg.get("delivery") or {}).get("quiet_hours")
    if not quiet or len(quiet) != 2:
        return False
    start, end = int(quiet[0]), int(quiet[1])
    hour = (now_local or datetime.now()).hour
    if start == end:
        return False
    if start < end:  # e.g. [1, 6]
        return start <= hour < end
    return hour >= start or hour < end  # wraps midnight, e.g. [22, 7]


def _sent_last_24h() -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM alert_events "
            "WHERE fired_at >= datetime('now', '-1 day')"
        ).fetchone()
    return int(row["n"])


def _last_fired(rule_id: str) -> Optional[str]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(fired_at) AS t FROM alert_events WHERE rule_id = ?",
            (rule_id,),
        ).fetchone()
    return row["t"] if row and row["t"] else None


def _in_cooldown(rule: dict) -> bool:
    last = _last_fired(rule["id"])
    if not last:
        return False
    cooldown = float(rule.get("cooldown_hours", 24))
    fired = datetime.fromisoformat(last.rstrip("Z"))
    if fired.tzinfo is None:
        fired = fired.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - fired < timedelta(hours=cooldown)


# ── ntfy delivery ────────────────────────────────────────────────────────────

def send_push(title: str, body: str, priority: str = "default") -> bool:
    """POST one notification to the configured ntfy topic. False = not sent."""
    topic = settings.NTFY_TOPIC
    if not topic:
        return False
    if priority not in _PRIORITIES:
        priority = "default"
    url = f"{settings.NTFY_SERVER.rstrip('/')}/{topic}"
    try:
        resp = requests.post(
            url,
            data=body.encode("utf-8"),
            headers={"Title": title, "Priority": priority, "Tags": "chart_with_downwards_trend"},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error("ntfy push failed: %s", e)
        return False


def _record_event(rule_id: str, title: str, body: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO alert_events (rule_id, fired_at, title, body) "
            "VALUES (?, ?, ?, ?)",
            (rule_id, datetime.now(timezone.utc).isoformat(), title, body),
        )


# ── Engine ───────────────────────────────────────────────────────────────────

def evaluate_alerts() -> int:
    """Evaluate every enabled rule; deliver what passes the gates.

    Returns the number of pushes sent. Silent no-op without NTFY_TOPIC.
    """
    if not settings.NTFY_TOPIC:
        return 0
    cfg = load_alerts_config()
    rules = cfg.get("rules") or []
    if not rules:
        return 0

    max_per_day = int((cfg.get("delivery") or {}).get("max_per_day", 5))
    quiet = _in_quiet_hours(cfg)
    sent = 0

    for rule in rules:
        if not rule.get("enabled", False):
            continue
        evaluator = _EVALUATORS.get(rule.get("type", ""))
        if evaluator is None:
            logger.warning("Alert rule %s: unknown type %r", rule.get("id"), rule.get("type"))
            continue
        if _in_cooldown(rule):
            continue
        try:
            body = evaluator(rule)
        except Exception as e:
            logger.error("Alert rule %s evaluation error: %s", rule.get("id"), e)
            continue
        if body is None:
            continue
        # Condition is TRUE. Delivery gates: quiet hours + daily cap. A gated
        # rule is NOT recorded, so it stays armed and fires when the gate lifts.
        if quiet:
            logger.info("Alert %s suppressed (quiet hours): %s", rule["id"], body)
            continue
        if _sent_last_24h() + sent >= max_per_day:
            logger.warning("Alert %s suppressed (daily cap %d)", rule["id"], max_per_day)
            continue
        title = rule.get("title") or rule["id"]
        if send_push(title, body, rule.get("priority", "default")):
            _record_event(rule["id"], title, body)
            sent += 1
            logger.info("Alert fired: %s — %s", title, body)

    return sent


def get_alerts_status() -> dict:
    """Config + state summary for GET /api/alerts/status."""
    cfg = load_alerts_config()
    rules = []
    for rule in cfg.get("rules") or []:
        rules.append({
            "id": rule.get("id"),
            "type": rule.get("type"),
            "enabled": bool(rule.get("enabled", False)),
            "cooldown_hours": rule.get("cooldown_hours", 24),
            "priority": rule.get("priority", "default"),
            "title": rule.get("title"),
            "last_fired": _last_fired(rule.get("id", "")),
        })
    with get_conn() as conn:
        recent = [dict(r) for r in conn.execute(
            "SELECT rule_id, fired_at, title, body FROM alert_events "
            "ORDER BY fired_at DESC LIMIT 20"
        )]
    return {
        "enabled": bool(settings.NTFY_TOPIC),
        "server": settings.NTFY_SERVER,
        "delivery": cfg.get("delivery") or {},
        "sent_last_24h": _sent_last_24h(),
        "rules": rules,
        "recent": recent,
        "config_path": str(ALERTS_YAML),
    }
