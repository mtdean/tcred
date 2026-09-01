"""Cover data/gmail_ingest.py — Meco newsletter ingest over Gmail IMAP.

Strategy:
  * `message_to_article` is pure given raw RFC822 bytes — build real MIME
    messages with the stdlib and test the row contract.
  * `fetch_meco_newsletters` runs against a fake IMAP connection installed
    via monkeypatched `_connect`; credentials come from patched settings.
  * Persistence + idempotency verified against the test DB.
"""

from __future__ import annotations

from email.message import EmailMessage

import pytest

from cache import db
from data import gmail_ingest as gi


NOW = "2026-09-01T12:00:00+00:00"
LONG_BODY = "<p>" + "Spreads tightened while issuance surged. " * 40 + "</p>"


def _mime(
    subject="Net Interest: Bank Funding",
    sender='"Net Interest" <marc@netinterest.co>',
    msgid="<abc123@mail.example>",
    date="Mon, 31 Aug 2026 09:30:00 -0400",
    html=LONG_BODY,
    plain="plain fallback body",
) -> bytes:
    msg = EmailMessage()
    if subject is not None:
        msg["Subject"] = subject
    msg["From"] = sender
    if msgid:
        msg["Message-ID"] = msgid
    if date:
        msg["Date"] = date
    msg.set_content(plain)
    if html is not None:
        msg.add_alternative(html, subtype="html")
    return bytes(msg)


# ─── message_to_article ──────────────────────────────────────────────────────
class TestMessageToArticle:
    def test_full_row_contract(self):
        row = gi.message_to_article(_mime(), NOW)
        assert row is not None
        assert row["title"] == "Net Interest: Bank Funding"
        assert row["feed_name"] == "Net Interest"
        assert row["published_at"] == "2026-08-31T13:30:00+00:00"  # UTC
        assert row["fetched_at"] == NOW
        assert "rfc822msgid:abc123%40mail.example" in row["url"]
        assert len(row["id"]) == 16
        # Long HTML body → stored as full text, stripped of tags.
        assert row["content_text"] is not None
        assert "<" not in row["content_text"]
        assert row["snippet"] is not None and len(row["snippet"]) <= 500

    def test_source_type_derived_from_sender_as_letter(self):
        # "Net Interest" matches the research sub-group in feeds.py.
        row = gi.message_to_article(_mime(), NOW)
        assert row["source_type"] == "research"
        row2 = gi.message_to_article(
            _mime(sender='"Random Letter" <x@y.com>', msgid="<r@y>"), NOW
        )
        assert row2["source_type"] == "letter"

    def test_default_category_is_newsletter(self):
        row = gi.message_to_article(_mime(), NOW)
        assert row["feed_category"] == "newsletter"

    def test_sender_category_override(self, monkeypatch):
        monkeypatch.setattr(
            gi, "load_data_sources",
            lambda: {"gmail_ingest": {"sender_categories": {"net interest": "credit"}}},
        )
        row = gi.message_to_article(_mime(), NOW)
        assert row["feed_category"] == "credit"

    def test_short_body_yields_no_content_text(self):
        row = gi.message_to_article(_mime(html="<p>short teaser</p>"), NOW)
        assert row["content_text"] is None
        assert row["snippet"] == "short teaser"

    def test_plain_text_fallback_when_no_html_part(self):
        long_plain = "word " * 400
        row = gi.message_to_article(_mime(html=None, plain=long_plain), NOW)
        assert row["content_text"] is not None
        assert row["content_text"].startswith("word word")

    def test_missing_subject_returns_none(self):
        assert gi.message_to_article(_mime(subject=None), NOW) is None

    def test_missing_message_id_still_stable(self):
        raw = _mime(msgid=None)
        a = gi.message_to_article(raw, NOW)
        b = gi.message_to_article(raw, NOW)
        assert a["id"] == b["id"]

    def test_id_differs_per_message(self):
        a = gi.message_to_article(_mime(msgid="<one@x>"), NOW)
        b = gi.message_to_article(_mime(msgid="<two@x>"), NOW)
        assert a["id"] != b["id"]


# ─── fetch_meco_newsletters ──────────────────────────────────────────────────
class FakeIMAP:
    """Minimal imaplib stand-in: fixed message set, records the mailbox.

    `mailboxes` drives list(); select() succeeds only on an exact (quoted)
    match against them — mirroring Gmail's case-sensitive mailbox names.
    """

    def __init__(self, messages: dict[bytes, bytes], mailboxes=("Meco",)):
        self.messages = messages
        self.mailboxes = list(mailboxes)
        self.selected = None

    def select(self, mailbox, readonly=False):
        if mailbox.strip('"') not in self.mailboxes:
            return ("NO", [b"[NONEXISTENT] Unknown Mailbox"])
        self.selected = mailbox
        return ("OK", [b"1"])

    def list(self):
        return ("OK", [
            b'(\\HasNoChildren) "/" "' + m.encode() + b'"' for m in self.mailboxes
        ])

    def search(self, charset, *criteria):
        return ("OK", [b" ".join(self.messages.keys())])

    def fetch(self, num, spec):
        return ("OK", [(num + b" (RFC822)", self.messages[num])])

    def logout(self):
        return ("BYE", [b""])


@pytest.fixture
def gmail_creds(monkeypatch):
    monkeypatch.setattr(gi.settings, "GMAIL_ADDRESS", "me@gmail.com")
    monkeypatch.setattr(gi.settings, "GMAIL_APP_PASSWORD", "apppassword")
    monkeypatch.setattr(gi.settings, "GMAIL_MECO_LABEL", "Meco")


class TestFetchMecoNewsletters:
    def test_noop_without_credentials(self, fresh_db, monkeypatch):
        monkeypatch.setattr(gi.settings, "GMAIL_ADDRESS", "")
        monkeypatch.setattr(
            gi, "_connect",
            lambda: (_ for _ in ()).throw(AssertionError("must not connect")),
        )
        assert gi.fetch_meco_newsletters() == 0

    def test_inserts_and_is_idempotent(self, fresh_db, gmail_creds, monkeypatch):
        fake = FakeIMAP({
            b"1": _mime(msgid="<m1@x>", subject="Issue 1"),
            b"2": _mime(msgid="<m2@x>", subject="Issue 2"),
        })
        monkeypatch.setattr(gi, "_connect", lambda: fake)

        assert gi.fetch_meco_newsletters() == 2
        assert fake.selected == '"Meco"'

        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT title, content_text, source_type FROM articles ORDER BY title"
            ).fetchall()
        assert [r["title"] for r in rows] == ["Issue 1", "Issue 2"]
        assert all(r["content_text"] for r in rows)

        # Second run: same messages → nothing new.
        assert gi.fetch_meco_newsletters() == 0

    def test_no_matching_label_returns_zero(self, fresh_db, gmail_creds, monkeypatch):
        fake = FakeIMAP({}, mailboxes=["INBOX", "Receipts"])
        monkeypatch.setattr(gi, "_connect", lambda: fake)
        assert gi.fetch_meco_newsletters() == 0

    def test_label_resolves_case_insensitively(
        self, fresh_db, gmail_creds, monkeypatch
    ):
        # Configured "Meco" vs Gmail's actual "Meco_<uuid>"-style casing.
        monkeypatch.setattr(gi.settings, "GMAIL_MECO_LABEL", "meco_abc-123")
        fake = FakeIMAP(
            {b"1": _mime(msgid="<m1@x>", subject="Issue 1")},
            mailboxes=["INBOX", "Meco_abc-123"],
        )
        monkeypatch.setattr(gi, "_connect", lambda: fake)
        assert gi.fetch_meco_newsletters() == 1
        assert fake.selected == '"Meco_abc-123"'

    def test_sole_meco_prefixed_mailbox_is_fallback(
        self, fresh_db, gmail_creds, monkeypatch
    ):
        # Configured label matches nothing, but exactly one "meco*" mailbox exists.
        monkeypatch.setattr(gi.settings, "GMAIL_MECO_LABEL", "Meco")
        fake = FakeIMAP(
            {b"1": _mime(msgid="<m1@x>", subject="Issue 1")},
            mailboxes=["INBOX", "Meco_e9acf9f0-uuid"],
        )
        monkeypatch.setattr(gi, "_connect", lambda: fake)
        assert gi.fetch_meco_newsletters() == 1
        assert fake.selected == '"Meco_e9acf9f0-uuid"'

    def test_unparseable_message_skipped_others_survive(
        self, fresh_db, gmail_creds, monkeypatch
    ):
        fake = FakeIMAP({
            b"1": _mime(subject=None, msgid="<skip@x>"),  # no subject → None
            b"2": _mime(msgid="<keep@x>", subject="Kept"),
        })
        monkeypatch.setattr(gi, "_connect", lambda: fake)
        assert gi.fetch_meco_newsletters() == 1

    def test_limit_takes_newest(self, fresh_db, gmail_creds, monkeypatch):
        fake = FakeIMAP({
            b"1": _mime(msgid="<old@x>", subject="Old"),
            b"2": _mime(msgid="<new@x>", subject="New"),
        })
        monkeypatch.setattr(gi, "_connect", lambda: fake)
        assert gi.fetch_meco_newsletters(limit=1) == 1
        with db.get_conn() as conn:
            rows = conn.execute("SELECT title FROM articles").fetchall()
        assert [r["title"] for r in rows] == ["New"]
