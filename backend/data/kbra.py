"""
backend/data/kbra.py — KBRA Presale Parser (Phase 7).

Manual ingest workflow:
  1. User downloads presale PDFs from kbra.com (free account) into the
     configured presale_dir (default: backend/data/kbra/presales/).
  2. User presses the PARSE button in the UI — this fires
     POST /api/kbra/refresh, which calls process_kbra_presales().
  3. We scan the dir for unseen PDFs, extract text with pdfminer, send the
     executive-summary excerpt to Claude for structured field extraction,
     and persist one row per PDF into kbra_presales.

Hard constraint: Claude extraction is MANUAL only. There is no scheduler job
for KBRA (verified in data/scheduler.py); the only entrypoint is the route
handler. Do not add a scheduler hook.

Failure mode is per-PDF: if a single PDF fails to parse we log and continue
to the next file rather than aborting the whole batch.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import anthropic

from cache.db import get_conn
from config import load_data_sources, settings

logger = logging.getLogger(__name__)

# Match data/abs_parser.py — single source of truth for the Claude model used
# by ingest extractors. Bump here, not in scattered call sites.
CLAUDE_MODEL = "claude-sonnet-4-6"


KBRA_EXTRACTION_PROMPT = """You are a structured finance analyst. Extract key assumptions
from this KBRA presale report excerpt.

Return ONLY JSON, no preamble:
{
  "deal_name": "string",
  "issuer": "string",
  "asset_class": "string (e.g. prime_auto_loan, consumer_loan, equipment)",
  "closing_date": "YYYY-MM-DD or null",
  "base_cdr": <float % or null>,
  "base_cpr": <float % or null>,
  "base_loss_rate": <float % cumulative net loss or null>,
  "base_severity": <float % or null>,
  "ce_aaa": <float % credit enhancement for AAA or null>,
  "ce_aa": <float % or null>,
  "ce_a": <float % or null>,
  "ce_bbb": <float % or null>,
  "ce_bb": <float % or null>
}

PRESALE EXCERPT:
"""


_CE_FIELDS = ("ce_aaa", "ce_aa", "ce_a", "ce_bbb", "ce_bb")
_EXTRACTED_FIELDS = (
    "deal_name",
    "issuer",
    "asset_class",
    "closing_date",
    "base_cdr",
    "base_cpr",
    "base_loss_rate",
    "base_severity",
    *_CE_FIELDS,
)


def _derive_confidence(data: dict) -> str:
    """Confidence from CE coverage: all 5 → high, some → medium, none → low.

    CE-by-rating is the most rigorously stated field in a KBRA presale and the
    one downstream consumers most often filter on, so it's a good proxy for
    whether the extraction caught the assumptions table cleanly.
    """
    present = sum(1 for f in _CE_FIELDS if data.get(f) is not None)
    if present == len(_CE_FIELDS):
        return "high"
    if present == 0:
        return "low"
    return "medium"


def _resolve_presale_dir() -> Path:
    """Anchor the configured (relative) presale_dir to the backend directory.

    settings.DB_PATH is built as Path(__file__).parent / "cache" / "monitor.db"
    in config.py, so the backend root is one level up from this file's parent.
    Using __file__ keeps the resolution independent of the process CWD.
    """
    cfg = load_data_sources()
    rel = cfg.get("kbra", {}).get("presale_dir", "data/kbra/presales")
    backend_dir = Path(__file__).resolve().parent.parent
    return backend_dir / rel


_claude_client: anthropic.Anthropic | None = None


def _get_claude_client() -> anthropic.Anthropic:
    global _claude_client
    if _claude_client is None:
        _claude_client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _claude_client


def process_kbra_presales() -> int:
    """Scan presale_dir for new PDFs, extract assumptions via Claude, persist.

    MANUAL only — the route handler is the sole caller. Returns the count of
    PDFs successfully processed in this batch.
    """
    presale_dir = _resolve_presale_dir()
    if not presale_dir.exists():
        logger.info(
            "KBRA presale dir missing — create %s and drop PDFs",
            presale_dir,
        )
        return 0

    try:
        # Local import: pdfminer.six is heavy and only needed for this manual
        # path. Keeping it local lets the rest of the module import cleanly
        # even if the package isn't installed (e.g. in syntax-only CI).
        from pdfminer.high_level import extract_text
    except ImportError:
        logger.error("pdfminer.six not installed — run: pip install pdfminer.six")
        return 0

    if not settings.ANTHROPIC_API_KEY:
        logger.error("KBRA extraction requires ANTHROPIC_API_KEY — skipping")
        return 0

    with get_conn() as conn:
        existing = {
            r[0]
            for r in conn.execute(
                "SELECT pdf_filename FROM kbra_presales WHERE pdf_filename IS NOT NULL"
            ).fetchall()
        }

    client = _get_claude_client()
    processed = 0

    for pdf_path in sorted(presale_dir.glob("*.pdf")):
        if pdf_path.name in existing:
            continue

        try:
            text = extract_text(str(pdf_path))
            excerpt = text[:5000]  # exec summary; assumptions live up top

            response = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=800,
                messages=[
                    {
                        "role": "user",
                        "content": KBRA_EXTRACTION_PROMPT + excerpt,
                    }
                ],
            )
            raw = response.content[0].text.strip()
            # Strip ```json fences if Claude returned them despite instructions.
            if raw.startswith("```"):
                raw = raw.split("```", 2)[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            data = json.loads(raw.strip())

        except Exception as e:
            logger.error("KBRA parse error [%s]: %s", pdf_path.name, e)
            continue

        row_id = hashlib.sha256(pdf_path.name.encode()).hexdigest()[:16]
        now = datetime.now(timezone.utc).isoformat()

        row = {
            "id": row_id,
            "pdf_filename": pdf_path.name,
            "extracted_text": text[:5000],
            "parsed_at": now,
            "parse_confidence": _derive_confidence(data),
            **{k: data.get(k) for k in _EXTRACTED_FIELDS},
        }

        try:
            with get_conn() as conn:
                cols = ", ".join(row.keys())
                placeholders = ", ".join(f":{k}" for k in row.keys())
                conn.execute(
                    f"INSERT OR IGNORE INTO kbra_presales ({cols}) VALUES ({placeholders})",
                    row,
                )
        except Exception as e:
            logger.error("KBRA store error [%s]: %s", pdf_path.name, e)
            continue

        existing.add(pdf_path.name)
        processed += 1
        logger.info("KBRA presale processed: %s", pdf_path.name)

    logger.info("KBRA parse complete — %d presales processed", processed)
    return processed


def get_kbra_presales(
    asset_class: Optional[str] = None,
    limit: int = 100,
) -> list[dict]:
    """Filtered query for the UI. Most recent first."""
    sql = (
        "SELECT id, deal_name, issuer, asset_class, closing_date, pdf_filename, "
        "base_cdr, base_cpr, base_loss_rate, base_severity, "
        "ce_aaa, ce_aa, ce_a, ce_bbb, ce_bb, "
        "parse_confidence, parsed_at "
        "FROM kbra_presales"
    )
    params: list = []
    if asset_class:
        sql += " WHERE asset_class = ?"
        params.append(asset_class)
    sql += " ORDER BY parsed_at DESC LIMIT ?"
    params.append(limit)

    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()

    return [dict(r) for r in rows]
