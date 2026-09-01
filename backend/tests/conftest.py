"""
Shared pytest fixtures.

Test isolation strategy:
  * MONITOR_DB_PATH is set BEFORE backend modules import, so settings.DB_PATH
    points at a session temp file instead of cache/monitor.db. cache.db then
    captures that path at import time.
  * The `fresh_db` fixture rebuilds the schema between tests so state never
    leaks across cases.
  * Network calls (requests/aiohttp/anthropic/fredapi/yfinance) are mocked at
    the fixture level — real tests must not hit the internet.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ─── Path & env setup BEFORE backend imports ──────────────────────────────────
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Use a single throwaway DB file for the whole session. Per-test isolation is
# done by truncating in `fresh_db`. Set BEFORE importing config/cache.db.
_SESSION_TMP = Path(tempfile.mkdtemp(prefix="situmon-tests-"))
os.environ["MONITOR_DB_PATH"] = str(_SESSION_TMP / "monitor.db")
os.environ.setdefault("FRED_API_KEY", "test-fred-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("EDGAR_USER_AGENT", "TestRunner/0.1 test@example.com")
# Force-blank (not setdefault): config.py's load_dotenv() won't override
# existing env vars, so this guarantees the developer's real Gmail creds in
# .env never reach a test run — gmail ingest must stay a no-op unless a test
# patches credentials in explicitly.
os.environ["GMAIL_ADDRESS"] = ""
os.environ["GMAIL_APP_PASSWORD"] = ""


# Now safe to import backend modules. They'll all see the test DB path.
import cache.db as db_module  # noqa: E402


# ─── Per-test fresh DB ────────────────────────────────────────────────────────
@pytest.fixture
def fresh_db():
    """Wipe + recreate every table so each test starts from empty state."""
    with db_module.get_conn() as conn:
        # Drop everything then re-run the schema. Cheaper than a new file.
        # FTS5 virtual tables go first: dropping one removes its shadow tables
        # (articles_fts_data etc.), which cannot be dropped directly.
        conn.execute("DROP TABLE IF EXISTS articles_fts")
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        for row in rows:
            name = row[0]
            if name.startswith("sqlite_"):
                continue
            conn.execute(f"DROP TABLE IF EXISTS {name}")
    db_module.init_db()
    yield db_module.DB_PATH


@pytest.fixture
def db_conn(fresh_db):
    """Convenience: a context-managed connection on the fresh test DB."""
    with db_module.get_conn() as conn:
        yield conn


# ─── Sample-file loader ───────────────────────────────────────────────────────
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def load_fixture():
    """Read a text file from tests/fixtures/."""
    def _load(name: str, encoding: str = "utf-8") -> str:
        return (FIXTURES_DIR / name).read_text(encoding=encoding)
    return _load


@pytest.fixture
def load_fixture_bytes():
    def _load(name: str) -> bytes:
        return (FIXTURES_DIR / name).read_bytes()
    return _load


# ─── Anthropic mock ──────────────────────────────────────────────────────────
class _StubAnthropicMessage:
    def __init__(self, text: str):
        self.content = [MagicMock(text=text)]


@pytest.fixture
def mock_anthropic(monkeypatch):
    """
    Replace anthropic.Anthropic() with a stub whose `.messages.create(...)`
    returns whatever text the test sets via `.next_text(...)`.
    """
    state = {"text": "[]"}

    class _StubMessages:
        def create(self, **kwargs):
            return _StubAnthropicMessage(state["text"])

    class _StubClient:
        def __init__(self, *a, **kw):
            self.messages = _StubMessages()

    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", _StubClient)

    class _Handle:
        def next_text(self, text: str) -> None:
            state["text"] = text

    return _Handle()


# ─── requests mock (responses library) ────────────────────────────────────────
@pytest.fixture
def mocked_responses():
    """Use the `responses` lib to stub out requests.* calls inside a test."""
    import responses as _resp

    with _resp.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        yield rsps


# ─── aiohttp mock (aioresponses) ──────────────────────────────────────────────
@pytest.fixture
def mocked_aiohttp():
    from aioresponses import aioresponses

    with aioresponses() as m:
        yield m


# ─── FastAPI TestClient ──────────────────────────────────────────────────────
@pytest.fixture
def api_client(fresh_db):
    """A TestClient over the FastAPI app, scoped to a fresh DB."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.routes import router

    # Stand up a minimal app — skip the lifespan (scheduler) so the test
    # process doesn't start background jobs.
    app = FastAPI()
    app.include_router(router, prefix="/api")
    with TestClient(app) as client:
        yield client
