"""
Cover data/kbra.py — KBRA presale parser.

The PDF→text→Claude pipeline is too heavy to fixture end-to-end (pdfminer
needs real binary PDFs), so this file covers:
  * Pure helpers — `_derive_confidence`, `_resolve_presale_dir`.
  * `process_kbra_presales` happy path with the presale dir mocked to point at
    an empty/missing directory.
  * Query helper `get_kbra_presales` seeded directly via SQL since the schema
    is well-defined.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cache import db
from data import kbra


NOW = "2026-05-29T12:00:00+00:00"


# ─── _derive_confidence ───────────────────────────────────────────────────────
class TestDeriveConfidence:
    def test_all_ce_fields_present_returns_high(self):
        data = {f: 5.0 for f in kbra._CE_FIELDS}
        assert kbra._derive_confidence(data) == "high"

    def test_no_ce_fields_returns_low(self):
        assert kbra._derive_confidence({}) == "low"

    def test_some_ce_fields_returns_medium(self):
        data = {"ce_aaa": 25.0, "ce_aa": 18.0}  # 2 of 5 present
        assert kbra._derive_confidence(data) == "medium"

    def test_none_values_count_as_missing(self):
        data = {f: None for f in kbra._CE_FIELDS}
        assert kbra._derive_confidence(data) == "low"

    def test_extra_fields_are_ignored(self):
        data = {
            "deal_name": "Carvana 2026-1",
            "base_cdr": 4.0,
            **{f: 5.0 for f in kbra._CE_FIELDS},
        }
        assert kbra._derive_confidence(data) == "high"

    @pytest.mark.parametrize("present_count, expected", [
        (0, "low"),
        (1, "medium"),
        (2, "medium"),
        (3, "medium"),
        (4, "medium"),
        (5, "high"),
    ])
    def test_threshold_table(self, present_count, expected):
        data = {f: 5.0 for f in kbra._CE_FIELDS[:present_count]}
        assert kbra._derive_confidence(data) == expected


# ─── _resolve_presale_dir ─────────────────────────────────────────────────────
class TestResolvePresaleDir:
    def test_anchors_to_backend_dir(self):
        path = kbra._resolve_presale_dir()
        # Whatever the configured rel path is, it must hang off backend/.
        backend = Path(kbra.__file__).resolve().parent.parent
        assert path.is_absolute()
        assert backend in path.parents or path == backend


# ─── _EXTRACTED_FIELDS sanity ─────────────────────────────────────────────────
class TestExtractedFieldsContract:
    def test_includes_all_ce_fields(self):
        for f in kbra._CE_FIELDS:
            assert f in kbra._EXTRACTED_FIELDS

    def test_includes_required_metadata(self):
        for f in ("deal_name", "issuer", "asset_class", "closing_date"):
            assert f in kbra._EXTRACTED_FIELDS


# ─── process_kbra_presales: short-circuit paths ───────────────────────────────
class TestProcessKbraPresalesShortCircuits:
    def test_missing_presale_dir_returns_zero(self, fresh_db, monkeypatch):
        # Point the resolver at a guaranteed-missing path.
        missing = Path("/nonexistent/path/that/does/not/exist")
        monkeypatch.setattr(kbra, "_resolve_presale_dir", lambda: missing)
        assert kbra.process_kbra_presales() == 0

    def test_empty_presale_dir_returns_zero(self, fresh_db, monkeypatch, tmp_path):
        # Dir exists but no PDFs in it — main loop never iterates → 0.
        monkeypatch.setattr(kbra, "_resolve_presale_dir", lambda: tmp_path)
        # Make sure the key is present (set in conftest) so we exercise the
        # "0 PDFs found" branch, not the early "no API key" branch.
        from config import settings
        original = settings.ANTHROPIC_API_KEY
        settings.ANTHROPIC_API_KEY = "test-key"
        try:
            assert kbra.process_kbra_presales() == 0
        finally:
            settings.ANTHROPIC_API_KEY = original

    def test_no_api_key_returns_zero(self, fresh_db, monkeypatch, tmp_path):
        # Dir exists and contains one PDF, but ANTHROPIC_API_KEY is empty.
        # Function must short-circuit before invoking the (stubbed) client.
        pdf = tmp_path / "FAKE_PRESALE.pdf"
        pdf.write_bytes(b"%PDF-1.4\n% not a real PDF\n")
        monkeypatch.setattr(kbra, "_resolve_presale_dir", lambda: tmp_path)

        from config import settings
        original = settings.ANTHROPIC_API_KEY
        settings.ANTHROPIC_API_KEY = ""
        try:
            result = kbra.process_kbra_presales()
        finally:
            settings.ANTHROPIC_API_KEY = original
        assert result == 0


# ─── process_kbra_presales: happy path with mocks ────────────────────────────
class TestProcessKbraPresalesHappyPath:
    def test_persists_row_with_extracted_fields(
        self, fresh_db, monkeypatch, tmp_path, mock_anthropic
    ):
        pdf = tmp_path / "carvana_presale.pdf"
        pdf.write_bytes(b"%PDF-1.4\nstub\n")
        monkeypatch.setattr(kbra, "_resolve_presale_dir", lambda: tmp_path)

        # Stub pdfminer's extract_text by patching the module attribute that
        # process_kbra_presales imports inside the function. We do the patch
        # at the import path it uses.
        import pdfminer.high_level as ph
        monkeypatch.setattr(
            ph, "extract_text",
            lambda _path: "Executive summary text that fits within 5000 chars",
        )

        # Reset the cached Claude client so the conftest-patched anthropic
        # class is the one instantiated.
        monkeypatch.setattr(kbra, "_claude_client", None)

        mock_anthropic.next_text(
            '{"deal_name": "Carvana 2026-1", "issuer": "Carvana", '
            '"asset_class": "subprime_auto_loan", "closing_date": "2026-06-15", '
            '"base_cdr": 4.0, "base_cpr": 12.0, "base_loss_rate": 8.5, '
            '"base_severity": 65.0, "ce_aaa": 35.0, "ce_aa": 28.0, '
            '"ce_a": 22.0, "ce_bbb": 16.0, "ce_bb": 10.0}'
        )

        n = kbra.process_kbra_presales()
        assert n == 1

        with db.get_conn() as conn:
            rows = conn.execute("SELECT * FROM kbra_presales").fetchall()
        assert len(rows) == 1
        r = dict(rows[0])
        assert r["deal_name"] == "Carvana 2026-1"
        assert r["asset_class"] == "subprime_auto_loan"
        assert r["ce_aaa"] == pytest.approx(35.0)
        assert r["pdf_filename"] == "carvana_presale.pdf"
        # All 5 CE fields present → high confidence.
        assert r["parse_confidence"] == "high"

    def test_strips_json_code_fence_from_claude_response(
        self, fresh_db, monkeypatch, tmp_path, mock_anthropic
    ):
        pdf = tmp_path / "stub.pdf"
        pdf.write_bytes(b"%PDF\n")
        monkeypatch.setattr(kbra, "_resolve_presale_dir", lambda: tmp_path)

        import pdfminer.high_level as ph
        monkeypatch.setattr(ph, "extract_text", lambda _p: "stub text")
        monkeypatch.setattr(kbra, "_claude_client", None)

        # Claude often returns markdown-fenced JSON despite the prompt.
        mock_anthropic.next_text(
            '```json\n{"deal_name": "X", "issuer": "Y", '
            '"asset_class": "consumer_loan", "closing_date": null, '
            '"base_cdr": null, "base_cpr": null, "base_loss_rate": null, '
            '"base_severity": null, "ce_aaa": null, "ce_aa": null, '
            '"ce_a": null, "ce_bbb": null, "ce_bb": null}\n```'
        )

        n = kbra.process_kbra_presales()
        assert n == 1
        with db.get_conn() as conn:
            row = conn.execute("SELECT deal_name, parse_confidence "
                               "FROM kbra_presales").fetchone()
        assert row["deal_name"] == "X"
        # All CE NULL → low confidence.
        assert row["parse_confidence"] == "low"

    def test_unparseable_response_is_skipped_not_raised(
        self, fresh_db, monkeypatch, tmp_path, mock_anthropic
    ):
        pdf = tmp_path / "bad.pdf"
        pdf.write_bytes(b"%PDF\n")
        monkeypatch.setattr(kbra, "_resolve_presale_dir", lambda: tmp_path)
        import pdfminer.high_level as ph
        monkeypatch.setattr(ph, "extract_text", lambda _p: "text")
        monkeypatch.setattr(kbra, "_claude_client", None)
        # Return garbage that json.loads can't parse — must be caught.
        mock_anthropic.next_text("not valid json at all {")
        assert kbra.process_kbra_presales() == 0

    def test_already_processed_pdfs_are_skipped(
        self, fresh_db, monkeypatch, tmp_path, mock_anthropic
    ):
        pdf = tmp_path / "already_seen.pdf"
        pdf.write_bytes(b"%PDF\n")
        monkeypatch.setattr(kbra, "_resolve_presale_dir", lambda: tmp_path)

        # Pre-insert a row whose pdf_filename matches the on-disk file.
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO kbra_presales(id, pdf_filename, parsed_at) "
                "VALUES('x', 'already_seen.pdf', ?)",
                (NOW,),
            )

        import pdfminer.high_level as ph
        # If we accidentally try to process, this would raise → test would fail.
        monkeypatch.setattr(
            ph, "extract_text",
            lambda _p: (_ for _ in ()).throw(RuntimeError("should not be called")),
        )
        monkeypatch.setattr(kbra, "_claude_client", None)
        assert kbra.process_kbra_presales() == 0


# ─── get_kbra_presales ────────────────────────────────────────────────────────
def _seed_kbra_row(id_: str, asset_class: str, deal_name: str,
                   parsed_at: str = NOW) -> None:
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO kbra_presales(id, deal_name, asset_class, "
            "pdf_filename, parsed_at, parse_confidence) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (id_, deal_name, asset_class, f"{id_}.pdf", parsed_at, "high"),
        )


class TestGetKbraPresales:
    def test_returns_empty_when_no_rows(self, fresh_db):
        assert kbra.get_kbra_presales() == []

    def test_filters_by_asset_class(self, fresh_db):
        _seed_kbra_row("a1", "subprime_auto_loan", "Carvana")
        _seed_kbra_row("a2", "consumer_loan", "SoFi")
        rows = kbra.get_kbra_presales(asset_class="subprime_auto_loan")
        assert [r["deal_name"] for r in rows] == ["Carvana"]

    def test_orders_by_parsed_at_desc(self, fresh_db):
        _seed_kbra_row("old", "consumer_loan", "Old", parsed_at="2026-01-01T00:00:00")
        _seed_kbra_row("new", "consumer_loan", "New", parsed_at="2026-05-29T00:00:00")
        rows = kbra.get_kbra_presales()
        assert [r["deal_name"] for r in rows] == ["New", "Old"]

    def test_respects_limit(self, fresh_db):
        for i in range(5):
            _seed_kbra_row(f"r{i}", "consumer_loan", f"D{i}",
                           parsed_at=f"2026-05-{20+i:02d}T00:00:00")
        rows = kbra.get_kbra_presales(limit=2)
        assert len(rows) == 2

    def test_returns_expected_columns(self, fresh_db):
        _seed_kbra_row("r1", "consumer_loan", "D")
        rows = kbra.get_kbra_presales()
        assert rows
        # Per the SELECT in get_kbra_presales.
        expected = {
            "id", "deal_name", "issuer", "asset_class", "closing_date",
            "pdf_filename", "base_cdr", "base_cpr", "base_loss_rate",
            "base_severity", "ce_aaa", "ce_aa", "ce_a", "ce_bbb", "ce_bb",
            "parse_confidence", "parsed_at",
        }
        assert expected == set(rows[0].keys())
