"""
Cover data/alerts.py — rule evaluation, delivery gates, ntfy push.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from cache import db
from data import alerts


NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)


def _seed_metric(conn, series_id: str, date: str, value: float):
    conn.execute(
        "INSERT OR REPLACE INTO metrics (series_id, date, value, label, category, fetched_at) "
        "VALUES (?, ?, ?, ?, 'macro', ?)",
        (series_id, date, value, series_id, NOW.isoformat()),
    )
    conn.commit()


def _seed_event(conn, rule_id: str, hours_ago: float):
    fired = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    conn.execute(
        "INSERT INTO alert_events (rule_id, fired_at, title, body) VALUES (?, ?, 't', 'b')",
        (rule_id, fired),
    )
    conn.commit()


# ─── Evaluators ───────────────────────────────────────────────────────────────
class TestMetricThreshold:
    def test_fires_above(self, db_conn):
        _seed_metric(db_conn, "HY", "2026-09-17", 4.5)
        rule = {"id": "r", "series_id": "HY", "op": "gt", "value": 4.0}
        assert "4.5" in alerts._eval_metric_threshold(rule)

    def test_quiet_below(self, db_conn):
        _seed_metric(db_conn, "HY", "2026-09-17", 2.7)
        rule = {"id": "r", "series_id": "HY", "op": "gt", "value": 4.0}
        assert alerts._eval_metric_threshold(rule) is None

    def test_lt_direction(self, db_conn):
        _seed_metric(db_conn, "X", "2026-09-17", -1.0)
        rule = {"id": "r", "series_id": "X", "op": "lt", "value": 0}
        assert alerts._eval_metric_threshold(rule) is not None

    def test_missing_series_is_quiet(self, fresh_db):
        rule = {"id": "r", "series_id": "NOPE", "op": "gt", "value": 0}
        assert alerts._eval_metric_threshold(rule) is None


class TestMetricDelta:
    def test_fires_on_rise(self, db_conn):
        _seed_metric(db_conn, "HY", "2026-09-10", 3.0)
        _seed_metric(db_conn, "HY", "2026-09-17", 3.6)
        rule = {"id": "r", "series_id": "HY", "days": 5, "change": 0.5}
        assert alerts._eval_metric_delta(rule) is not None

    def test_quiet_on_small_move(self, db_conn):
        _seed_metric(db_conn, "HY", "2026-09-10", 3.0)
        _seed_metric(db_conn, "HY", "2026-09-17", 3.2)
        rule = {"id": "r", "series_id": "HY", "days": 5, "change": 0.5}
        assert alerts._eval_metric_delta(rule) is None

    def test_negative_change_watches_falls(self, db_conn):
        _seed_metric(db_conn, "PX", "2026-09-10", 100.0)
        _seed_metric(db_conn, "PX", "2026-09-17", 92.0)
        rule = {"id": "r", "series_id": "PX", "days": 5, "change": -5}
        assert alerts._eval_metric_delta(rule) is not None


class TestJobFailures:
    def test_fires_on_consecutive_errors(self, fresh_db):
        for _ in range(3):
            rid = db.start_job_run("edgar")
            db.finish_job_run(rid, "error", error="boom")
        out = alerts._eval_job_failures({"id": "r", "consecutive": 3})
        assert "edgar" in out

    def test_quiet_when_recent_success(self, fresh_db):
        for status in ("error", "success", "error"):
            rid = db.start_job_run("edgar")
            db.finish_job_run(rid, status)
        assert alerts._eval_job_failures({"id": "r", "consecutive": 3}) is None


class TestFeedHealth:
    def test_counts_dead_feeds(self, db_conn):
        for i in range(6):
            db_conn.execute(
                "INSERT INTO feed_health (feed_name, url, is_live, last_checked) "
                "VALUES (?, ?, 0, ?)",
                (f"feed{i}", f"http://x/{i}", NOW.isoformat()),
            )
        db_conn.commit()
        out = alerts._eval_feed_health({"id": "r", "min_dead": 5})
        assert "6 feeds down" in out

    def test_quiet_below_threshold(self, db_conn):
        db_conn.execute(
            "INSERT INTO feed_health (feed_name, url, is_live, last_checked) "
            "VALUES ('f', 'http://x', 0, ?)",
            (NOW.isoformat(),),
        )
        db_conn.commit()
        assert alerts._eval_feed_health({"id": "r", "min_dead": 5}) is None


class TestSectorMarkDrop:
    def _seed_sector(self, conn, period, sector, cost, fv, n=6):
        import hashlib
        for i in range(n):  # >=5 BDCs so get_bdc_sector_trend keeps the period
            conn.execute(
                "INSERT OR REPLACE INTO bdc_industry "
                "(id, cik, bdc_name, period, industry_raw, sector, cost_basis, fair_value, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (hashlib.sha256(f"{i}|{period}|{sector}".encode()).hexdigest()[:16],
                 str(i), f"BDC{i}", period, sector, sector, cost, fv, NOW.isoformat()),
            )
        conn.commit()

    def test_fires_on_big_drop(self, db_conn):
        self._seed_sector(db_conn, "2026-03-31", "Software & Tech", 100, 99)
        self._seed_sector(db_conn, "2026-06-30", "Software & Tech", 100, 96)
        out = alerts._eval_bdc_sector_mark_drop({"id": "r", "drop_bps": 150})
        assert "Software & Tech" in out

    def test_quiet_on_small_drop(self, db_conn):
        self._seed_sector(db_conn, "2026-03-31", "Software & Tech", 100, 99)
        self._seed_sector(db_conn, "2026-06-30", "Software & Tech", 100, 98.5)
        assert alerts._eval_bdc_sector_mark_drop({"id": "r", "drop_bps": 150}) is None


# ─── Delivery gates ───────────────────────────────────────────────────────────
class TestQuietHours:
    @pytest.mark.parametrize("hour, expected", [
        (23, True), (2, True), (6, True), (7, False), (12, False), (21, False),
    ])
    def test_wrapping_window(self, hour, expected):
        cfg = {"delivery": {"quiet_hours": [22, 7]}}
        now = datetime(2026, 9, 18, hour, 0)
        assert alerts._in_quiet_hours(cfg, now) is expected

    def test_no_window_means_never_quiet(self):
        assert alerts._in_quiet_hours({}, datetime(2026, 9, 18, 3, 0)) is False


class TestCooldown:
    def test_recent_fire_blocks(self, db_conn):
        _seed_event(db_conn, "r1", hours_ago=2)
        assert alerts._in_cooldown({"id": "r1", "cooldown_hours": 24}) is True

    def test_old_fire_allows(self, db_conn):
        _seed_event(db_conn, "r1", hours_ago=30)
        assert alerts._in_cooldown({"id": "r1", "cooldown_hours": 24}) is False

    def test_never_fired_allows(self, fresh_db):
        assert alerts._in_cooldown({"id": "new", "cooldown_hours": 24}) is False


# ─── Engine end-to-end ───────────────────────────────────────────────────────
def _config(rules, max_per_day=5, quiet=None):
    return {
        "delivery": {"max_per_day": max_per_day, "quiet_hours": quiet or []},
        "rules": rules,
    }


class TestEvaluateAlerts:
    @pytest.fixture(autouse=True)
    def _topic(self, monkeypatch):
        monkeypatch.setattr(alerts.settings, "NTFY_TOPIC", "test-topic")
        monkeypatch.setattr(alerts.settings, "NTFY_SERVER", "https://ntfy.example")

    def test_noop_without_topic(self, fresh_db, monkeypatch):
        monkeypatch.setattr(alerts.settings, "NTFY_TOPIC", "")
        assert alerts.evaluate_alerts() == 0

    def test_fires_and_records(self, db_conn, monkeypatch, mocked_responses):
        _seed_metric(db_conn, "HY", "2026-09-17", 4.5)
        rule = {"id": "r1", "enabled": True, "type": "metric_threshold",
                "series_id": "HY", "op": "gt", "value": 4.0, "title": "HY hot"}
        monkeypatch.setattr(alerts, "load_alerts_config", lambda: _config([rule]))
        mocked_responses.post("https://ntfy.example/test-topic", status=200)
        assert alerts.evaluate_alerts() == 1
        with db.get_conn() as conn:
            rows = conn.execute("SELECT * FROM alert_events").fetchall()
        assert len(rows) == 1 and rows[0]["rule_id"] == "r1"

    def test_disabled_rule_skipped(self, db_conn, monkeypatch):
        _seed_metric(db_conn, "HY", "2026-09-17", 4.5)
        rule = {"id": "r1", "enabled": False, "type": "metric_threshold",
                "series_id": "HY", "op": "gt", "value": 4.0}
        monkeypatch.setattr(alerts, "load_alerts_config", lambda: _config([rule]))
        assert alerts.evaluate_alerts() == 0

    def test_cooldown_blocks_repeat(self, db_conn, monkeypatch, mocked_responses):
        _seed_metric(db_conn, "HY", "2026-09-17", 4.5)
        _seed_event(db_conn, "r1", hours_ago=1)
        rule = {"id": "r1", "enabled": True, "type": "metric_threshold",
                "series_id": "HY", "op": "gt", "value": 4.0, "cooldown_hours": 24}
        monkeypatch.setattr(alerts, "load_alerts_config", lambda: _config([rule]))
        assert alerts.evaluate_alerts() == 0

    def test_quiet_hours_suppress_but_do_not_record(self, db_conn, monkeypatch):
        _seed_metric(db_conn, "HY", "2026-09-17", 4.5)
        rule = {"id": "r1", "enabled": True, "type": "metric_threshold",
                "series_id": "HY", "op": "gt", "value": 4.0}
        monkeypatch.setattr(alerts, "load_alerts_config",
                            lambda: _config([rule], quiet=[0, 24]))
        monkeypatch.setattr(alerts, "_in_quiet_hours", lambda cfg, now=None: True)
        assert alerts.evaluate_alerts() == 0
        with db.get_conn() as conn:
            n = conn.execute("SELECT COUNT(*) FROM alert_events").fetchone()[0]
        assert n == 0  # stays armed for the post-quiet run

    def test_daily_cap(self, db_conn, monkeypatch, mocked_responses):
        for i in range(5):
            _seed_event(db_conn, f"old{i}", hours_ago=3)
        _seed_metric(db_conn, "HY", "2026-09-17", 4.5)
        rule = {"id": "r1", "enabled": True, "type": "metric_threshold",
                "series_id": "HY", "op": "gt", "value": 4.0}
        monkeypatch.setattr(alerts, "load_alerts_config",
                            lambda: _config([rule], max_per_day=5))
        assert alerts.evaluate_alerts() == 0

    def test_push_failure_not_recorded(self, db_conn, monkeypatch, mocked_responses):
        _seed_metric(db_conn, "HY", "2026-09-17", 4.5)
        rule = {"id": "r1", "enabled": True, "type": "metric_threshold",
                "series_id": "HY", "op": "gt", "value": 4.0}
        monkeypatch.setattr(alerts, "load_alerts_config", lambda: _config([rule]))
        mocked_responses.post("https://ntfy.example/test-topic", status=500)
        assert alerts.evaluate_alerts() == 0
        with db.get_conn() as conn:
            n = conn.execute("SELECT COUNT(*) FROM alert_events").fetchone()[0]
        assert n == 0  # cooldown untouched → retried next hour


class TestStatus:
    def test_status_shape(self, fresh_db, monkeypatch):
        monkeypatch.setattr(alerts.settings, "NTFY_TOPIC", "t")
        out = alerts.get_alerts_status()
        assert out["enabled"] is True
        assert isinstance(out["rules"], list)
        assert "sent_last_24h" in out
