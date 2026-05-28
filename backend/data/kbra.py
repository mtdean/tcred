"""
backend/data/kbra.py — KBRA Presale Parser (Phase 7).

Foundation stub. The kbra-presale-parser worktree implements:
  - process_kbra_presales(): scan data/kbra/presales/ for new PDFs, extract
                              text with pdfminer.six, send executive summary
                              to Claude for structured extraction, persist.
                              MANUAL only — triggered via the PARSE button.
  - get_kbra_presales(...):  filtered query for the UI

Auth complexity (KBRA requires a free account to download presales) is avoided
by treating ingest as semi-manual: user drops PDFs into the directory, button
processes any unseen filenames.
"""

from typing import Optional


def process_kbra_presales() -> int:
    return 0


def get_kbra_presales(
    asset_class: Optional[str] = None,
    limit: int = 100,
) -> list[dict]:
    return []
