"""
backend/data/gmail_ingest.py — Meco newsletter ingester (Gmail IMAP).

Meco's Gmail integration files every newsletter under a "Meco" label in the
connected Gmail account (they skip the inbox but stay in Gmail). This module
pulls recent messages from that label over IMAP, extracts the full HTML body,
and inserts each issue into the articles pipeline — content_text set, so the
classifier scores it and the summarizer writes a 2-3 sentence summary on the
next feeds cycle.

Setup (.env):
  GMAIL_ADDRESS       — the Gmail account Meco is connected to
  GMAIL_APP_PASSWORD  — app password from myaccount.google.com/apppasswords
                        (requires 2-step verification)
  GMAIL_MECO_LABEL    — optional, defaults to "Meco"

When the credentials are absent the fetcher is a silent no-op, so the feeds
job runs unchanged on machines without Gmail configured.

Optional per-sender category overrides in data_sources.yaml:
  gmail_ingest:
    sender_categories:        # case-insensitive substring of the sender name
      "fintech takes": fintech
Unmatched senders land in the 'newsletter' category.
"""

import email
import email.policy
import email.utils
import hashlib
import imaplib
import logging
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Optional

from config import load_data_sources, settings
from data.feeds import (
    MAX_FULL_TEXT_CHARS,
    MIN_FULL_TEXT_CHARS,
    derive_source_type,
    html_to_text,
    strip_html,
)

logger = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"
DEFAULT_LABEL = "Meco"
DEFAULT_CATEGORY = "newsletter"


def _sender_category(feed_name: str) -> str:
    """Map a sender display name to a feed category via yaml overrides."""
    overrides = (load_data_sources().get("gmail_ingest") or {}).get(
        "sender_categories"
    ) or {}
    name_l = feed_name.lower()
    for sub, category in overrides.items():
        if sub.lower() in name_l:
            return category
    return DEFAULT_CATEGORY


def _extract_body_html(msg) -> str:
    """Best body part of a MIME message: prefer text/html, fall back to plain."""
    html_part, plain_part = None, None
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype == "text/html" and html_part is None:
            html_part = part
        elif ctype == "text/plain" and plain_part is None:
            plain_part = part
    part = html_part or plain_part
    if part is None:
        return ""
    try:
        return part.get_content()
    except Exception as e:  # undecodable charset etc.
        logger.debug("Body decode failed: %s", e)
        return ""


def message_to_article(raw: bytes, fetched_at: str) -> Optional[dict]:
    """Parse one raw RFC822 message into an articles-table row dict.

    Returns None for messages without a subject (nothing to display).
    """
    msg = email.message_from_bytes(raw, policy=email.policy.default)

    title = strip_html(msg.get("Subject", "") or "")
    if not title:
        return None

    display, addr = email.utils.parseaddr(msg.get("From", "") or "")
    feed_name = display or addr or "Unknown sender"

    msgid = (msg.get("Message-ID", "") or "").strip().strip("<>")
    if not msgid:
        # No Message-ID (rare) — synthesize a stable one from sender + subject
        # + date so refetches still dedupe.
        msgid = f"{addr}|{title}|{msg.get('Date', '')}"

    published_at = None
    try:
        dt = email.utils.parsedate_to_datetime(msg.get("Date", "") or "")
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            published_at = dt.astimezone(timezone.utc).isoformat()
    except Exception as e:
        logger.debug("Unparseable Date header: %s", e)

    # Paragraph-preserving body for the in-app reader; flat text for the snippet.
    body = html_to_text(_extract_body_html(msg))
    flat = " ".join(body.split())
    content_text = (
        body[:MAX_FULL_TEXT_CHARS] if len(body) >= MIN_FULL_TEXT_CHARS else None
    )

    # Deep link that opens this exact message in Gmail's web UI.
    url = (
        "https://mail.google.com/mail/u/0/#search/rfc822msgid:"
        + urllib.parse.quote(msgid, safe="")
    )

    return {
        "id": hashlib.sha256(f"gmail:{msgid}".encode()).hexdigest()[:16],
        "feed_name": feed_name,
        "feed_category": _sender_category(feed_name),
        "title": title,
        "snippet": flat[:500] or None,
        "url": url,
        "published_at": published_at,
        "fetched_at": fetched_at,
        "source_type": derive_source_type(feed_name, "letter"),
        "content_text": content_text,
    }


def _connect() -> imaplib.IMAP4_SSL:
    conn = imaplib.IMAP4_SSL(IMAP_HOST)
    conn.login(settings.GMAIL_ADDRESS, settings.GMAIL_APP_PASSWORD)
    return conn


_LIST_NAME_RE = re.compile(rb'"((?:[^"\\]|\\.)*)"\s*$')


def _resolve_label(conn, label: str) -> Optional[str]:
    """Find the actual mailbox for `label`, tolerating case differences.

    Gmail IMAP mailbox names are case-sensitive and Meco creates labels like
    "Meco_<uuid>", so an exact select of the configured value can fail on
    casing alone. Falls back to (1) a case-insensitive match of the configured
    label, then (2) the sole mailbox starting with "meco" if there is exactly
    one. Returns None when nothing matches.
    """
    typ, boxes = conn.list()
    if typ != "OK":
        return None
    names = []
    for line in boxes or []:
        m = _LIST_NAME_RE.search(line or b"")
        if m:
            names.append(m.group(1).decode("utf-8", "replace"))
    for name in names:
        if name.lower() == label.lower():
            return name
    meco_like = [n for n in names if n.lower().startswith("meco")]
    if len(meco_like) == 1:
        return meco_like[0]
    logger.error(
        "Gmail label %r not found; mailboxes: %s", label, ", ".join(names)[:500]
    )
    return None


def fetch_meco_newsletters(days_back: int = 3, limit: int = 100) -> int:
    """Pull recent messages from the Meco label into `articles`. Returns count
    of newly inserted rows. No-op (0) when Gmail credentials aren't configured.
    """
    if not (settings.GMAIL_ADDRESS and settings.GMAIL_APP_PASSWORD):
        logger.debug("Gmail ingest skipped — GMAIL_ADDRESS/GMAIL_APP_PASSWORD unset")
        return 0

    label = settings.GMAIL_MECO_LABEL or DEFAULT_LABEL
    since = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime(
        "%d-%b-%Y"
    )

    conn = _connect()
    try:
        typ, _ = conn.select(f'"{label}"', readonly=True)
        if typ != "OK":
            resolved = _resolve_label(conn, label)
            if resolved is None:
                return 0
            logger.info("Gmail label %r resolved to %r", label, resolved)
            label = resolved
            typ, _ = conn.select(f'"{label}"', readonly=True)
            if typ != "OK":
                logger.error("Gmail label %r not selectable", label)
                return 0

        typ, data = conn.search(None, "SINCE", since)
        if typ != "OK":
            logger.error("Gmail search failed: %s", typ)
            return 0
        msg_nums = data[0].split()[-limit:]  # newest N (Gmail returns ascending)

        fetched_at = datetime.now(timezone.utc).isoformat()
        rows = []
        for num in msg_nums:
            typ, msg_data = conn.fetch(num, "(RFC822)")
            if typ != "OK" or not msg_data or msg_data[0] is None:
                continue
            try:
                row = message_to_article(msg_data[0][1], fetched_at)
            except Exception as e:
                logger.warning("Gmail message %s parse error: %s", num, e)
                continue
            if row:
                rows.append(row)
    finally:
        try:
            conn.logout()
        except Exception:
            pass

    if not rows:
        logger.info("Gmail ingest: no messages in label %r since %s", label, since)
        return 0

    from cache.db import get_conn, upsert_article

    ids = [r["id"] for r in rows]
    with get_conn() as db_conn:
        placeholders = ",".join("?" * len(ids))
        existing = {
            r[0]
            for r in db_conn.execute(
                f"SELECT id FROM articles WHERE id IN ({placeholders})", ids
            )
        }
    inserted = 0
    for row in rows:
        if row["id"] in existing:
            continue
        upsert_article(row)
        inserted += 1

    logger.info(
        "Gmail ingest: %d message(s) in window, %d new article(s)",
        len(rows), inserted,
    )
    return inserted
