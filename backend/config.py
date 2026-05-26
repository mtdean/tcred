"""
backend/config.py — loads YAML configs and env vars.
"""

import os
import sys
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent
ENV_PATH = PROJECT_ROOT / ".env"

if ENV_PATH.exists():
    load_dotenv(ENV_PATH)
else:
    # Remind the user how to set up credentials, then carry on with defaults.
    load_dotenv()  # fall back to a CWD search just in case
    print(
        "\n".join(
            [
                "",
                "  ┌─────────────────────────────────────────────────────────────┐",
                "  │  ⚠  No .env found — running without API credentials.          │",
                "  │                                                               │",
                "  │  News scoring, the AI digest, and FRED macro data will be     │",
                "  │  empty until you add keys. To fix:                            │",
                "  │                                                               │",
                "  │      cp .env.example .env                                     │",
                "  │      # then edit .env and set:                                │",
                "  │      #   FRED_API_KEY        (free: fred.stlouisfed.org)       │",
                "  │      #   ANTHROPIC_API_KEY   (console.anthropic.com)           │",
                "  │      #   EDGAR_USER_AGENT    (e.g. <TCRED>/0.1 you@email.com)  │",
                "  └─────────────────────────────────────────────────────────────┘",
                "",
            ]
        ),
        file=sys.stderr,
    )

CONFIG_DIR = PROJECT_ROOT / "config"


@lru_cache(maxsize=None)
def load_feeds() -> dict:
    with open(CONFIG_DIR / "feeds.yaml") as f:
        return yaml.safe_load(f)


@lru_cache(maxsize=None)
def load_data_sources() -> dict:
    with open(CONFIG_DIR / "data_sources.yaml") as f:
        return yaml.safe_load(f)


class Settings:
    FRED_API_KEY: str = os.getenv("FRED_API_KEY", "")
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

    # SEC EDGAR requires a User-Agent header identifying the requester
    EDGAR_USER_AGENT: str = os.getenv(
        "EDGAR_USER_AGENT", "SituationMonitor/0.1 contact@example.com"
    )

    CACHE_DIR: Path = Path(__file__).parent / "cache" / "store"
    DB_PATH: Path = Path(__file__).parent / "cache" / "monitor.db"

    def __post_init__(self):
        self.CACHE_DIR.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.CACHE_DIR.mkdir(parents=True, exist_ok=True)
