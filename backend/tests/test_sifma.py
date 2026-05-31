"""Cover data/sifma.py — parser + drop-folder ingest + routes."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from cache import db
from data import sifma


# ─── Helpers to build a SIFMA-shaped xlsx fixture ──────────────────────────

def _build_xlsx(path: Path, rows: list[list]) -> Path:
    """Write `rows` into a single-sheet xlsx and return its path."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "US ABS Issuance"
    for row in rows:
        ws.append(row)
    wb.save(str(path))
    return path


# Documented SIFMA layout (asset-class columns + a Date/Month column).
_HEADER = ["Date", "Auto", "Credit Cards", "Equipment", "Student Loans",
           "Home Equity", "Manufactured Housing", "Other", "Total"]


def _three_month_rows() -> list[list]:
    return [
        # SIFMA-style preface rows (these must be ignored).
        ["US ABS Issuance", None, None, None, None, None, None, None, None],
        ["Source: SIFMA", None, None, None, None, None, None, None, None],
        _HEADER,
        ["2026-03", 15.2, 5.1, 3.4, 2.0, 0.5, 0.2, 6.1, 32.5],
        ["2026-04", 17.0, 4.8, 3.1, 1.8, 0.6, 0.3, 6.5, 34.1],
        ["2026-05", 18.4, 5.4, 3.7, 2.2, 0.4, 0.1, 7.0, 37.2],
        # Footnote row (should not parse as a date).
        ["Notes: Provisional data subject to revision", None, None, None,
         None, None, None, None, None],
    ]


# ─── Normalization + header detection ──────────────────────────────────────
class TestClassifyHeader:
    def test_maps_known_classes(self):
        cmap = sifma._classify_header(
            ["Date", "Auto", "Credit Cards", "Equipment", "Student Loans",
             "Home Equity", "Manufactured Housing", "Other", "Total"]
        )
        assert cmap[1] == "SIFMA_AUTO"
        assert cmap[2] == "SIFMA_CC"
        assert cmap[3] == "SIFMA_EQUIP"
        assert cmap[4] == "SIFMA_STUDENT"
        assert cmap[5] == "SIFMA_HE"
        assert cmap[6] == "SIFMA_MH"
        assert cmap[7] == "SIFMA_OTHER"
        assert cmap[8] == "SIFMA_TOTAL"

    def test_first_match_wins(self):
        # Two Auto columns → only the first one is mapped.
        cmap = sifma._classify_header(["Date", "Auto", "Auto Lease"])
        assert cmap == {1: "SIFMA_AUTO"}

    def test_partial_layout(self):
        # SIFMA may add or remove columns over time; what's present should still map.
        cmap = sifma._classify_header(["Period", "Auto", "Credit Cards", "Total"])
        assert set(cmap.values()) == {"SIFMA_AUTO", "SIFMA_CC", "SIFMA_TOTAL"}


class TestFindHeaderRow:
    def test_skips_preface_rows(self):
        rows = _three_month_rows()
        assert sifma._find_header_row(rows) == 2  # 0/1 are preface, 2 is header

    def test_returns_none_when_no_recognizable_header(self):
        rows = [["random", "text"], ["more", "rows"]]
        assert sifma._find_header_row(rows) is None


# ─── Period parsing ────────────────────────────────────────────────────────
class TestParsePeriod:
    @pytest.mark.parametrize("raw, expected", [
        ("2026-04", "2026-04-01"),
        ("2026/04", "2026-04-01"),
        ("2026-04-15", "2026-04-01"),
        ("Apr 2026", "2026-04-01"),
        ("April 2026", "2026-04-01"),
        ("2026 Apr", "2026-04-01"),
        ("2026", "2026-01-01"),
        (2026, "2026-01-01"),
        (datetime(2026, 5, 31), "2026-05-01"),
        ("", None),
        (None, None),
        ("garbage", None),
        ("Notes: revised", None),
    ])
    def test_cases(self, raw, expected):
        assert sifma._parse_period(raw) == expected


# ─── End-to-end parse ──────────────────────────────────────────────────────
class TestParseXlsx:
    def test_three_month_fixture(self, tmp_path):
        f = _build_xlsx(tmp_path / "abs.xlsx", _three_month_rows())
        records = sifma.parse_sifma_xlsx(f)

        # 3 dates × 8 series = 24 records.
        assert len(records) == 24
        by_pair = {(r["series_id"], r["date"]): r["value"] for r in records}
        assert by_pair[("SIFMA_AUTO", "2026-04-01")] == 17.0
        assert by_pair[("SIFMA_TOTAL", "2026-05-01")] == 37.2
        assert all(r["category"] == "abs_issuance" for r in records)
        assert all(r["label"] for r in records)

    def test_unrecognized_xlsx_returns_empty(self, tmp_path):
        f = _build_xlsx(
            tmp_path / "bad.xlsx",
            [["random", "headers", "with", "no", "issuance", "info"]],
        )
        assert sifma.parse_sifma_xlsx(f) == []


# ─── Drop-folder ingest ────────────────────────────────────────────────────

@pytest.fixture
def drop_dirs(tmp_path, monkeypatch):
    """Redirect DROP_DIR + ARCHIVE_DIR to a per-test tmp tree."""
    drop = tmp_path / "drops"
    archive = drop / "parsed"
    monkeypatch.setattr(sifma, "DROP_DIR", drop)
    monkeypatch.setattr(sifma, "ARCHIVE_DIR", archive)
    return drop, archive


class TestIngestDrops:
    def test_no_drops_returns_zero(self, fresh_db, drop_dirs):
        out = sifma.ingest_sifma_drops()
        assert out["files"] == 0
        assert out["records"] == 0

    def test_parses_ingests_and_archives(self, fresh_db, drop_dirs):
        drop, archive = drop_dirs
        drop.mkdir(parents=True, exist_ok=True)
        f = _build_xlsx(drop / "abs.xlsx", _three_month_rows())

        out = sifma.ingest_sifma_drops()
        assert out["files"] == 1
        assert out["records"] == 24
        assert out["rows_written"] == 24

        # File moved into archive with timestamp prefix.
        assert not f.exists()
        archived = list(archive.glob("*__abs.xlsx"))
        assert len(archived) == 1

        # Metrics actually landed in the DB.
        with db.get_conn() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM metrics WHERE category = 'abs_issuance'"
            ).fetchone()[0]
        assert n == 24
        assert db.get_meta("last_sifma_ingest") is not None

    def test_unrecognized_file_moves_to_failed(self, fresh_db, drop_dirs):
        drop, archive = drop_dirs
        drop.mkdir(parents=True, exist_ok=True)
        f = _build_xlsx(drop / "junk.xlsx", [["no", "headers"], ["x", "y"]])

        out = sifma.ingest_sifma_drops()
        assert out["files"] == 1
        assert out["records"] == 0

        assert not f.exists()
        failed = list((archive / "__failed").glob("*__junk.xlsx"))
        assert len(failed) == 1

    def test_idempotent_after_archive(self, fresh_db, drop_dirs):
        drop, _ = drop_dirs
        drop.mkdir(parents=True, exist_ok=True)
        _build_xlsx(drop / "abs.xlsx", _three_month_rows())
        first = sifma.ingest_sifma_drops()
        second = sifma.ingest_sifma_drops()
        assert first["records"] == 24
        assert second == {"files": 0, "records": 0, "rows_written": 0}


# ─── Query helper + routes ─────────────────────────────────────────────────
class TestGetIssuanceSeries:
    def test_shape(self, fresh_db):
        # Seed two SIFMA points manually so we don't depend on the parser.
        for sid, val in (("SIFMA_AUTO", 12.3), ("SIFMA_CC", 4.5)):
            db.upsert_metric({
                "series_id": sid, "label": sifma._LABELS[sid],
                "category": "abs_issuance", "date": "2026-04-01",
                "value": val, "fetched_at": "2026-05-31T00:00:00+00:00",
            })
        out = sifma.get_issuance_series()
        assert "SIFMA_AUTO" in out["sifma"]
        assert out["sifma"]["SIFMA_AUTO"]["points"][0]["value"] == 12.3
        # Series we didn't seed are absent from the response.
        assert "SIFMA_STUDENT" not in out["sifma"]


class TestIssuanceRoutes:
    def test_get_returns_payload(self, api_client, fresh_db):
        resp = api_client.get("/api/abs/issuance")
        assert resp.status_code == 200
        body = resp.json()
        assert "sifma" in body and "fred_supplement" in body

    def test_refresh_with_empty_dir(self, api_client, fresh_db, drop_dirs):
        # Drop dir empty → 0 files; FRED stub disabled (no real key needed).
        resp = api_client.post("/api/abs/issuance/refresh")
        assert resp.status_code == 200
        body = resp.json()
        assert body["files"] == 0
