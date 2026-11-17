"""Tests for data/sources.py — the pluggable data-source registry."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import pytest

from cache import db
from config import PROJECT_ROOT
from data import sources
from data.sources import Panel, Series, Source, discover_sources, store_series

EXAMPLE = PROJECT_ROOT / "plugins" / "_example_treasury_rates.py"


def _write_plugin(d: Path, name: str, body: str) -> Path:
    p = d / name
    p.write_text("from data.sources import Source\n" + body)
    return p


def _discover(plugin_dir: Path, overrides: dict | None = None) -> dict[str, Source]:
    found = discover_sources(builtin_modules=[], plugin_dir=plugin_dir, overrides=overrides or {})
    return {s.id: s for s in found}


class TestBuiltins:
    def test_builtin_modules_register_with_existing_job_ids(self):
        found = discover_sources(plugin_dir=Path("/nonexistent"), overrides={})
        assert [s.id for s in found] == [
            "manheim", "cfpb", "trace", "sce", "sba", "nfib",
            "philly_cards", "fdic", "fdic_rates", "kcfed_sbls", "edgar_distress",
        ]
        assert all(s.origin == "builtin" for s in found)
        cadence = {s.id: s.every_hours for s in found}
        assert cadence == {
            "manheim": 24, "cfpb": 24, "trace": 12, "sce": 168, "sba": 168, "nfib": 168,
            "philly_cards": 168, "fdic": 168, "fdic_rates": 168, "kcfed_sbls": 168,
            "edgar_distress": 24,
        }

    def test_builtin_import_failure_is_logged_not_raised(self, caplog, tmp_path):
        with caplog.at_level(logging.ERROR):
            found = discover_sources(
                builtin_modules=["data.does_not_exist"], plugin_dir=tmp_path, overrides={},
            )
        assert found == []
        assert "data.does_not_exist failed to import" in caplog.text


class TestPluginDiscovery:
    def test_loads_source_and_sources_list(self, tmp_path):
        _write_plugin(tmp_path, "one.py", "SOURCE = Source(id='one', label='One', fetch=lambda: 1)\n")
        _write_plugin(
            tmp_path, "many.py",
            "SOURCES = [Source(id='a_src', label='A', fetch=lambda: 1),\n"
            "           Source(id='b_src', label='B', fetch=lambda: 2)]\n",
        )
        found = _discover(tmp_path)
        assert set(found) == {"one", "a_src", "b_src"}
        assert found["one"].origin == "one.py"

    def test_underscore_files_are_skipped(self, tmp_path):
        _write_plugin(tmp_path, "_template.py", "SOURCE = Source(id='tmpl', label='T', fetch=lambda: 1)\n")
        assert _discover(tmp_path) == {}

    def test_broken_plugin_does_not_block_others(self, tmp_path, caplog):
        (tmp_path / "broken.py").write_text("raise RuntimeError('boom')\n")
        _write_plugin(tmp_path, "ok.py", "SOURCE = Source(id='ok_src', label='OK', fetch=lambda: 1)\n")
        with caplog.at_level(logging.ERROR):
            found = _discover(tmp_path)
        assert set(found) == {"ok_src"}
        assert "broken.py failed to load: boom" in caplog.text

    @pytest.mark.parametrize("bad_id", ["Bad-Id", "x", "1abc", "has space"])
    def test_invalid_ids_rejected(self, tmp_path, bad_id):
        _write_plugin(tmp_path, "p.py", f"SOURCE = Source(id={bad_id!r}, label='X', fetch=lambda: 1)\n")
        assert _discover(tmp_path) == {}

    def test_reserved_core_job_id_rejected(self, tmp_path, caplog):
        _write_plugin(tmp_path, "p.py", "SOURCE = Source(id='market', label='X', fetch=lambda: 1)\n")
        with caplog.at_level(logging.ERROR):
            assert _discover(tmp_path) == {}
        assert "reserved core job id" in caplog.text

    def test_duplicate_id_keeps_first(self, tmp_path):
        _write_plugin(tmp_path, "a.py", "SOURCE = Source(id='dup', label='first', fetch=lambda: 1)\n")
        _write_plugin(tmp_path, "b.py", "SOURCE = Source(id='dup', label='second', fetch=lambda: 1)\n")
        assert _discover(tmp_path)["dup"].label == "first"

    def test_plugin_cannot_shadow_builtin_id(self, tmp_path):
        _write_plugin(tmp_path, "p.py", "SOURCE = Source(id='cfpb', label='impostor', fetch=lambda: 1)\n")
        found = discover_sources(builtin_modules=["data.cfpb"], plugin_dir=tmp_path, overrides={})
        assert [(s.id, s.origin) for s in found] == [("cfpb", "builtin")]

    def test_non_source_object_skipped(self, tmp_path):
        _write_plugin(tmp_path, "p.py", "SOURCE = {'id': 'nope'}\n")
        assert _discover(tmp_path) == {}

    def test_nonpositive_interval_rejected(self, tmp_path):
        _write_plugin(tmp_path, "p.py", "SOURCE = Source(id='zero', label='Z', fetch=lambda: 1, every_hours=0)\n")
        assert _discover(tmp_path) == {}


class TestOverrides:
    def test_enabled_and_every_hours_from_config(self, tmp_path):
        _write_plugin(tmp_path, "p.py", "SOURCE = Source(id='tunable', label='T', fetch=lambda: 1)\n")
        found = _discover(tmp_path, {"tunable": {"enabled": False, "every_hours": 6}})
        assert found["tunable"].enabled is False
        assert found["tunable"].every_hours == 6.0

    def test_enabled_sources_filters_disabled(self, monkeypatch):
        on = Source(id="on_src", label="on", fetch=lambda: 1)
        off = Source(id="off_src", label="off", fetch=lambda: 1, enabled=False)
        monkeypatch.setattr(sources, "get_sources", lambda: (on, off))
        assert [s.id for s in sources.enabled_sources()] == ["on_src"]


class TestStoreSeries:
    def test_upserts_points_into_metrics(self, fresh_db):
        n = store_series("PLUG_X", "Plugin X", [("2026-01-01", 1.5), ("2026-02-01T00:00:00", 2)])
        assert n == 2
        # Re-storing the same date replaces rather than duplicates.
        store_series("PLUG_X", "Plugin X", [("2026-02-01", 3.0)])
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT date, value, category FROM metrics WHERE series_id = 'PLUG_X' ORDER BY date"
            ).fetchall()
        assert [tuple(r) for r in rows] == [
            ("2026-01-01", 1.5, "custom"),
            ("2026-02-01", 3.0, "custom"),
        ]


class TestExamplePlugin:
    def test_template_is_skipped_but_valid_when_renamed(self, tmp_path):
        assert _discover(EXAMPLE.parent).get("treasury_rates") is None
        shutil.copy(EXAMPLE, tmp_path / "treasury_rates.py")
        src = _discover(tmp_path)["treasury_rates"]
        assert src.panel is not None
        assert [s.series_id for s in src.panel.series][0] == "TSY_AVG_RATE_MARKETABLE"

    def test_fetch_parses_api_rows(self, tmp_path, fresh_db, mocked_responses):
        shutil.copy(EXAMPLE, tmp_path / "treasury_rates.py")
        src = _discover(tmp_path)["treasury_rates"]
        mocked_responses.get(
            "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/"
            "v2/accounting/od/avg_interest_rates",
            json={"data": [
                {"record_date": "2026-10-31", "security_desc": "Total Marketable",
                 "avg_interest_rate_amt": "3.570"},
                {"record_date": "2026-10-31", "security_desc": "Treasury Bills",
                 "avg_interest_rate_amt": "4.014"},
                {"record_date": "2026-10-31", "security_desc": "TIPS",  # not tracked
                 "avg_interest_rate_amt": "1.0"},
            ]},
        )
        assert src.fetch() == 2
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT date, value FROM metrics WHERE series_id = 'TSY_AVG_RATE_MARKETABLE'"
            ).fetchone()
        assert tuple(row) == ("2026-10-01", 3.57)


class TestApiAndJobs:
    def test_sources_endpoint_lists_panel_and_last_run(self, api_client, monkeypatch):
        src = Source(
            id="demo_src", label="Demo", fetch=lambda: 1, origin="demo.py",
            panel=Panel(title="Demo", series=(Series("DEMO_A", "A"),), unit="pct"),
        )
        monkeypatch.setattr(sources, "get_sources", lambda: (src,))
        run_id = db.start_job_run("demo_src", triggered_by="manual")
        db.finish_job_run(run_id, "success", rows_ingested=7)

        body = api_client.get("/api/sources").json()
        (row,) = body["sources"]
        assert row["id"] == "demo_src"
        assert row["origin"] == "demo.py"
        assert row["panel"]["series"] == [{"series_id": "DEMO_A", "name": "A"}]
        assert row["last_run"]["rows_ingested"] == 7

    def test_builtin_sources_are_manual_jobs(self):
        from data.manual_jobs import MANUAL_JOBS
        for sid in ("manheim", "cfpb", "trace", "sce", "sba", "nfib"):
            assert sid in MANUAL_JOBS

    async def test_scheduler_registers_each_enabled_source(self, fresh_db, monkeypatch):
        from data import scheduler

        added: list[str] = []

        class FakeScheduler:
            def add_job(self, fn, trigger, id, **kw):
                added.append(id)

            def start(self):
                pass

        async def no_initial_fetch(auto_news):
            return None

        on = Source(id="plug_on", label="on", fetch=lambda: 1, every_hours=3)
        monkeypatch.setattr(scheduler, "AsyncIOScheduler", FakeScheduler)
        monkeypatch.setattr(scheduler, "_initial_fetch", no_initial_fetch)
        monkeypatch.setattr(scheduler, "enabled_sources", lambda: [on])

        await scheduler.start_scheduler()
        assert "plug_on" in added
        assert "market" in added  # core jobs still wired by hand
