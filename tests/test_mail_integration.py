"""End-to-end mail integration tests — use ``aiosmtpd`` to spin up a
fake SMTP server on an OS-assigned port, actually send a message, and
assert the captured envelope decodes correctly.

Marked ``@pytest.mark.integration`` so they're excluded from the
default ``make test`` path (same policy as the real-LLM provider
tests). Run explicitly with ``make test-integration``.

aiosmtpd runs locally with no credentials, so there's no
cost/rate-limit risk — the marker is about "anything that isn't a pure
mock" vs. "any external network".
"""

from __future__ import annotations

import asyncio
import socket

import pytest
from aiosmtpd.controller import Controller

from decafclaw.mail import send_mail

pytestmark = pytest.mark.integration


def _pick_free_port() -> int:
    """Return a likely-free ephemeral port.

    aiosmtpd's ``Controller`` trips over ``port=0`` during its readiness
    check (it connects back to ``self.port`` before the OS-bound port is
    known). Pre-binding a socket, grabbing its port, and releasing gives
    us the same parallel-safety without the quirk. Small TOCTOU window
    is acceptable for test fixtures.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _CollectingHandler:
    """aiosmtpd handler that stashes received envelopes for assertion."""

    def __init__(self):
        self.messages: list[dict] = []

    async def handle_DATA(self, server, session, envelope):
        self.messages.append({
            "mail_from": envelope.mail_from,
            "rcpt_tos": list(envelope.rcpt_tos),
            "content": envelope.content.decode("utf-8", errors="replace"),
        })
        return "250 Message accepted for delivery"


@pytest.fixture
async def fake_smtp(config):
    """Spin up an aiosmtpd server on localhost:<random> for one test."""
    handler = _CollectingHandler()
    port = _pick_free_port()
    controller = Controller(handler, hostname="127.0.0.1", port=port)
    controller.start()
    try:
        # Configure the real mail core to point at the fake server.
        config.email.enabled = True
        config.email.smtp_host = controller.hostname
        config.email.smtp_port = controller.port
        config.email.smtp_username = ""  # no AUTH on the fake
        config.email.smtp_password = ""
        config.email.use_tls = False     # aiosmtpd default is plain
        config.email.sender_address = "bot@example.com"
        yield handler, config
    finally:
        controller.stop()
        # Give the loop a tick to let any dangling tasks finish.
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_end_to_end_plain(fake_smtp):
    handler, config = fake_smtp
    await send_mail(
        config, to="alice@example.com",
        subject="hi", body="hello from the test",
    )
    assert len(handler.messages) == 1
    msg = handler.messages[0]
    assert msg["mail_from"] == "bot@example.com"
    assert msg["rcpt_tos"] == ["alice@example.com"]
    assert "hello from the test" in msg["content"]
    assert "Subject: hi" in msg["content"]


@pytest.mark.asyncio
async def test_end_to_end_with_attachment(fake_smtp, tmp_path):
    handler, config = fake_smtp
    attachment = tmp_path / "note.txt"
    attachment.write_text("attached contents")
    await send_mail(
        config, to="alice@example.com",
        subject="with attachment", body="see attached",
        attachments=[str(attachment)],
    )
    assert len(handler.messages) == 1
    content = handler.messages[0]["content"]
    # Body is present
    assert "see attached" in content
    # Attachment was base64-encoded or similar — just check filename made it in
    assert "note.txt" in content


@pytest.mark.asyncio
async def test_end_to_end_multiple_recipients(fake_smtp):
    handler, config = fake_smtp
    await send_mail(
        config, to=["a@example.com", "b@example.com"],
        subject="hi", body="to both",
    )
    assert handler.messages[0]["rcpt_tos"] == [
        "a@example.com", "b@example.com",
    ]


@pytest.mark.asyncio
async def test_end_to_end_multipart_alternative(fake_smtp):
    handler, config = fake_smtp
    await send_mail(
        config, to="alice@example.com",
        subject="rich", body="plain version",
        html_body="<p>html <b>version</b></p>",
    )
    content = handler.messages[0]["content"]
    assert "plain version" in content
    assert "html" in content.lower()
    assert "multipart/alternative" in content.lower()
