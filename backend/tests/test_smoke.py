"""Sanity check that the test harness wires up cleanly."""

import sqlite3


def test_db_path_is_redirected(fresh_db):
    assert "situmon-tests-" in str(fresh_db)
    assert fresh_db.exists()


def test_schema_initialized(db_conn):
    rows = db_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {r[0] for r in rows}
    # Spot-check a few critical tables. Full coverage lives in test_db.py.
    for required in ("articles", "metrics", "edgar_filings", "abs_new_issues",
                     "bdc_summary", "regulatory_actions", "kbra_presales"):
        assert required in names, f"missing table: {required}"


def test_anthropic_is_stubbed(mock_anthropic):
    import anthropic
    mock_anthropic.next_text("hello")
    client = anthropic.Anthropic()  # stubbed by the mock_anthropic fixture
    resp = client.messages.create(model="x", max_tokens=10, messages=[])
    assert resp.content[0].text == "hello"
